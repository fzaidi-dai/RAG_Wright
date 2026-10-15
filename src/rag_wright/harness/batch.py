"""One question, not five.

**A user-experience requirement, and the honest framing counts.** It is verified that a sibling subagent
*does* complete when its peer interrupts — an earlier design claim to the contrary was wrong — so five
separate questions would be **correct**. They would also arrive one at a time, each stopping everything,
with no sign that another was behind it. The requirement: *"a worker that would ask instead returns 'I need X',
and the orchestrator asks once rather than five times."*

**The second benefit is the less obvious one.** A need that reaches the orchestrator can sometimes be
answered *by the orchestrator* — it already knows which document the conversation settled on — and never
troubles the user at all. A worker that interrupts directly goes straight past the tier that had the answer.

**The channel is ONE STRING, which is what shaped this.** Probed rather than assumed: a subagent's state
updates do not reach its parent, and a worker is given only the tools it declares. Its final message,
arriving as the `task` tool's result, is the whole of what it can say. Making that a structured
`response_format` turns *"I need X"* from prose somebody parses into JSON this middleware can act on.

**And a worker cannot ask even if its prompt is ignored**, because it is never given `ask_the_user`. A
prompt that says "do not ask" is a suggestion; an absent tool is a fact.
"""

from __future__ import annotations

import json
from typing import Any, NotRequired

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langgraph.types import interrupt

#: The synchronous calls permitted before the `interrupt()`. Each is safe to run twice, which is the only
#: question that matters: reading state, reading the needs off the messages, and formatting a question.
#:
#: Maintained, like `confirm.py`'s, and for the same reason: forbidding every call would forbid `state.get`.
PURE_BEFORE_THE_INTERRUPT = {"get", "set", "blocked_reports", "question_for", "str", "list"}


class BatchingState(AgentState):
    """Which needs have already been put to the user.

    **Without this it is a loop.** The `task` results stay in the message history for the life of the
    thread, so a scan with no memory finds the same blocked report on every subsequent turn and asks the
    same question forever.
    """

    answered_needs: NotRequired[list[str]]


def blocked_reports(messages: Any, *, already_handled: set[str]) -> list[tuple[str, str]]:
    """The needs a turn produced, as `(tool_call_id, need)`.

    **Most tool results are not reports**, so this is deliberately narrow and never raises: a parser that
    threw on the first ordinary result would take the run down, and one that treated prose as a need would
    ask the user about a document listing.
    """
    found: list[tuple[str, str]] = []
    for message in messages:
        call_id = getattr(message, "tool_call_id", None)
        if call_id is None or call_id in already_handled:
            continue
        try:
            report = json.loads(str(getattr(message, "content", "") or ""))
        except (ValueError, TypeError):
            continue
        if not isinstance(report, dict) or report.get("status") != "blocked":
            continue
        # A worker that says it is blocked and names nothing would produce an empty question.
        need = str(report.get("need") or "").strip()
        if need:
            found.append((call_id, need))
    return found


def question_for(needs: list[str]) -> str:
    """One message carrying everything the run is waiting on.

    Numbered when there is more than one, so the user can see the whole cost of answering before they start
    and can answer all of it in a single reply.
    """
    if len(needs) == 1:
        return needs[0]
    listed = "\n".join(f"{index}. {need}" for index, need in enumerate(needs, start=1))
    return f"Before I continue, {len(needs)} things:\n{listed}"


class BatchTheQuestions(AgentMiddleware):
    """Collect what the workers could not decide, and ask the user once."""

    state_schema = BatchingState

    def before_model(self, state, runtime) -> dict[str, Any] | None:
        handled = set(state.get("answered_needs") or [])
        needs = blocked_reports(state.get("messages") or [], already_handled=handled)

        if not needs:
            return None

        question = question_for([need for _, need in needs])

        # **Everything above is a pure read, and everything below happens once.** Resume replays this node
        # from the top: marking the needs handled before the interrupt would make the replay find nothing
        # and drop the question entirely.
        answer = interrupt({
            "question": question,
            "kind": "batched_needs",
            "needs": [need for _, need in needs],
        })

        return {
            "answered_needs": [*handled, *[call_id for call_id, _ in needs]],
            # Framed, so the orchestrator reads a recorded answer rather than something indistinguishable
            # from the user's own next sentence -- as `<viewing>` and `<agent_memory>` are.
            "messages": [{"role": "user",
                          "content": f"<answers_to_your_questions>\n{question}\n\nThey answered: "
                                     f"{answer}\n</answers_to_your_questions>"}],
        }
