"""How many times one tier may hand the same work down.

**Measured live, twice, and neither setting was right.** The chat-manager delegates to the orchestrator,
reads back *"Complete -- nothing further is needed"*, and delegates again: **3 times** with reasoning on,
**24 times** with reasoning off (the latter alongside 49 model calls in one item). The right answer is 1,
occasionally 2. Both numbers are the model's judgement, so choosing between the two reasoning settings
picks which failure we prefer rather than fixing one.

**So this is a count, not a request.** `withhold.py` states the principle and a measured run paid for it: a
warning repeated in four places was obeyed zero times, and taking the parameter away fixed what a fifth
instruction would not. A dispatch past the bound never runs -- no model call, no subagent, no second copy
of a reply the tier already has. Twenty-three of those twenty-four were pure waste.

**The refusal is a signpost, not a wall.** `TheSubjectIsNotAFilesystem` is the precedent: a bare
`not found` invited seven more wrong guesses in one run, and saying what the thing actually was fixed it.
A tier told only "no" flails; this one is handed back what the earlier dispatch returned and told to
answer from it.

**A `ToolMessage`, never a raise.** An exception would reach `ToolErrorMiddleware`, find nothing in the
taxonomy and halt the run -- a dead session in place of a recoverable wrong turn. Same reasoning as
`WithheldTools` and `TheSubjectIsNotAFilesystem`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from rag_wright.harness.batch import answers_batched
from rag_wright.harness.clarify import answers_so_far
from rag_wright.harness.seen import run_key

logger = logging.getLogger(__name__)

#: The tool a tier hands work down with, named by DeepAgents (`subagents.py`: `StructuredTool.from_function(
#: name="task", ...)`). Confirmed in the installed source rather than recalled.
DISPATCH = "task"

#: **One. Never two, and never two at once** (decided 2026-09-26).
#:
#: **Because planning is the orchestrator's domain, not the conversation tier's.** A message carrying two
#: requests is decomposed by the tier whose job that is -- which then launches its own workers per its
#: plan and can return the pieces separately -- rather than by the chat-manager guessing from the wording.
#: Letting both tiers decompose duplicates the concern and puts the harder half in the weaker place.
#:
#: **It was 2, and that was my error.** The justification was that `mixed-request` "has two parts", but
#: that variant's own `expected_output` reads *"the listing answered directly; the sweep delegated"* --
#: which is ONE delegation. The allowance flattered every measurement taken under it.
#:
#: **One also makes concurrency impossible, which is the load-bearing half.** Several tool calls in one
#: turn become separate `Send`s (`factory.py:1964`) and run in PARALLEL in that superstep, so a tier
#: emitting two `task` calls would start two orchestrators side by side. Nothing in the harness would
#: notice: the only thing that ever did was `RubricMiddleware`, which crashed on it and was removed.
#: The check-and-increment below has no `await` between the read and the write, so two
#: parallel tool tasks cannot both pass it.
DISPATCHES_PER_RUN = 1


def _name_of(tool: Any) -> str:
    """A tool's name, whatever shape it arrives in. Mirrored from `withhold.py` for the same reason: a
    turn's tools can be `BaseTool` objects or raw schema dicts."""
    return getattr(tool, "name", None) or (tool.get("name", "") if isinstance(tool, dict) else str(tool))


@dataclass
class _Budget:
    """What one run has already handed down, and what came back."""

    spent: int = 0
    replies: list[str] = field(default_factory=list)
    #: The dispatches let through, by call id: one that interrupted is run again, same id, on the resume.
    passed: set[str] = field(default_factory=set)


class OnlySoManyDispatches(AgentMiddleware):
    """Bound the number of dispatches this tier may make for each thing the person asks in a run (PS-80)."""

    def __init__(self, *, limit: int = DISPATCHES_PER_RUN, tier: str = "chat-manager", spent: Any = None) -> None:
        super().__init__()
        self._limit = limit
        self._tier = tier
        #: The run's tally (`WhatTheRunSpent`), into which each dispatch is written as run or refused (PS-86).
        self._spent = spent
        self._runs: dict[str, _Budget] = {}

    @property
    def name(self) -> str:
        return f"{type(self).__name__}({self._limit})"

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        return handler(self._without_the_dispatch(request))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        return await handler(self._without_the_dispatch(request))

    def _without_the_dispatch(self, request: Any) -> Any:
        """The turn's tools, minus `task` once the run's budget is spent.

        **The half the first version was missing, and `withhold.py` had already written down why:** *a tool
        in the schema is a tool the model will try, which costs a call even when the refusal works.*
        Measured live: the refusal held -- only 2 dispatches ever RAN -- while the tier attempted **24**,
        at 156.6s for that one item. Refusing the reach without removing the temptation converts an
        unbounded loop into a bounded one, which is progress and is not the fix.

        **Rebuilt only when something is actually removed**, as `WithheldTools` does: `override` returns a
        new request, and doing that every turn for no change is work the tier does not need.
        """
        budget = self._runs.get(_asked(request, getattr(request, "messages", None)))
        if budget is None or budget.spent < self._limit:
            return request
        tools = getattr(request, "tools", None) or []
        # **A spent orchestrator reports** (PS-86): every tool goes, not only `task`. Measured live with only `task`
        # taken away: 22 more model calls and 14 tool calls on its filesystem and todo list, until the run's budget
        # ended it on a cap message, which became the run's answer. With `ask_the_user` kept it asked the person,
        # twice of two, whether to dispatch more, which it could not. A worker's question needs no tool here:
        # `BatchTheQuestions` asks it in code, and the person's answer re-opens the allowance (`_asked`).
        if self._tier == "orchestrator":
            if not tools:
                return request
            # **And it is told why** (the signpost, above): with the tools gone and nothing said, its reply was its
            # plan, twice of two, not a report. Added to this call only; the tier's state is not written.
            spent = HumanMessage(content=f"<dispatches_spent>You have dispatched {budget.spent} worker(s) for this "
                                         "request, the most this run allows. Write your report now, from what the "
                                         "workers returned.</dispatches_spent>")
            return request.override(tools=[], tool_choice=None, messages=[*(request.messages or []), spent])
        kept = [tool for tool in tools if _name_of(tool) != DISPATCH]
        if len(kept) == len(tools):
            return request
        return request.override(tools=kept)

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        budget = self._budget(request)
        if budget is None:
            return handler(request)
        refusal = self._refuse(request, budget)
        if refusal is not None:
            return refusal
        result = handler(request)
        self._remember(budget, result)
        return result

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        budget = self._budget(request)
        if budget is None:
            return await handler(request)
        refusal = self._refuse(request, budget)
        if refusal is not None:
            return refusal
        result = await handler(request)
        self._remember(budget, result)
        return result

    def _budget(self, request: Any) -> _Budget | None:
        """This run's budget, or `None` when the call is not a dispatch at all.

        **Ordinary tools are untouched.** A bound that throttled reads would break the tier it protects,
        and the tier's own tools are how it answers without delegating in the first place.
        """
        call = getattr(request, "tool_call", None) or {}
        if call.get("name") != DISPATCH:
            return None
        return self._runs.setdefault(_asked(request, (getattr(request, "state", None) or {}).get("messages")),
                                     _Budget())

    def _refuse(self, request: Any, budget: _Budget) -> Any:
        call = request.tool_call
        # **A replay is the same dispatch** (PS-81): a worker's question interrupts inside it, and the resume runs
        # this call again, same id, to carry the person's answer down. Refused, the answer went nowhere.
        if call.get("id") and call["id"] in budget.passed:
            return None
        if budget.spent < self._limit:
            budget.spent += 1
            budget.passed.add(call.get("id") or "")
            self._tally(request, "dispatched")
            return None
        self._tally(request, "refused")

        from langchain_core.messages import ToolMessage

        # A call refused beside the one that ran, in the same turn, is answered before that one returns (PS-86).
        already = "\n\n".join(budget.replies[-2:]) or ("(none yet: the dispatch that ran in this same turn "
                                                       "returns beside this message -- answer from its reply)")
        return ToolMessage(
            content=f"{self._instead(budget.spent)}\n\nWhat came back:\n{already}",
            tool_call_id=call.get("id", ""),
            name=DISPATCH,
        )

    def _instead(self, spent: int) -> str:
        """What the tier does instead: the chat-manager answers the person; the orchestrator, who never talks to
        them, reports from what its workers returned (PS-86)."""
        if self._tier == "orchestrator":
            return (f"You have dispatched {spent} worker(s) for this request, the most this run allows. Do not "
                    "dispatch again -- write your report from what the workers returned.")
        return (f"You have already delegated this request {spent} time(s) and it came back. "
                "Do not delegate it again -- answer the user from what you have.")

    def _tally(self, request: Any, outcome: str) -> None:
        if self._spent is None:
            return
        spend = self._spent.of(run_key(request)).of(self._tier)
        getattr(spend, outcome).append(str(request.tool_call.get("args", {}).get("subagent_type", "")))

    def _remember(self, budget: _Budget, result: Any) -> None:
        """Keep what came back, so a refusal can hand it over instead of merely saying no."""
        content = getattr(result, "content", None)
        if content:
            budget.replies.append(str(content))


def _asked(request: Any, messages: list | None) -> str:
    """The run and the thing the person asked in it: their opening message, then each answer to a clarification.

    **An answer is new intent** (PS-80), so the allowance is per thing asked, not per run: a turn resumed on an answer
    may still delegate, once, though the run delegated before it asked. **Whichever way it arrives** (PS-86): to the
    tier's own `ask_the_user`, or to a worker's question that `BatchTheQuestions` asked, so an orchestrator at its bound
    can still carry the answer down to the worker that needed it.
    """
    return f"{run_key(request)}|{answers_so_far(messages or []) + answers_batched(messages or [])}"


def dispatch_bound(limit: int = DISPATCHES_PER_RUN, *, tier: str = "chat-manager",
                   spent: Any = None) -> OnlySoManyDispatches:
    """The bound a tier is built with: the chat-manager's `DISPATCHES_PER_RUN`, the orchestrator's
    `RunBudget.dispatches` (PS-86)."""
    return OnlySoManyDispatches(limit=limit, tier=tier, spent=spent)


def _reported(messages: list, *, asking: str) -> bool:
    """Whether a dispatch has come back with a report since the person last spoke.

    **The person speaks twice over**: a new message, and an answer to `asking`, which arrives as that tool's reply.
    An orchestrator that came back `blocked` has its question put to the person, and the tier must be able to act
    on the answer. **Matched by call id, not by the reply's name**: DeepAgents builds the dispatch's reply as
    `ToolMessage(content, tool_call_id=...)` with no name. A failed dispatch (`status="error"`) did not do the work,
    so it is not a report.
    """
    reported = False
    dispatches: set[str] = set()
    questions: set[str] = set()
    for message in messages:
        if isinstance(message, HumanMessage):
            reported = False
        elif isinstance(message, AIMessage):
            dispatches |= {call.get("id") for call in message.tool_calls if call.get("name") == DISPATCH}
            questions |= {call.get("id") for call in message.tool_calls if call.get("name") == asking}
        elif isinstance(message, ToolMessage):
            if message.tool_call_id in questions:
                reported = False
            elif message.tool_call_id in dispatches and message.status != "error":
                reported = True
    return reported


class TheReportIsTheAnswer(AgentMiddleware):
    """A tier handed a report answers from it (PS-76).

    **`OnlySoManyDispatches` took the dispatch away and left the direct tools**, and TexWright measured what the
    tier did with them: one question, the right route and a complete report every time, and in two runs of four the
    chat-manager went on to re-derive the report with its own reads -- 6 metered calls became 35, and 31k input
    tokens 330k, for the same answer. Planning is the orchestrator's, and it holds every operation the product
    exposes, so the direct tools give the tier back nothing the report lacked.

    **So once a report has come back this turn, the next call has none of the direct tools.** `ask_the_user` stays,
    so a question the orchestrator came back `blocked` on still reaches the person, and so do the agent's own
    filesystem and memory. The person's next message, or their answer to that question, restores the tools: the
    report answers the question it was asked for, not the next one. On the chat-manager only: the orchestrator
    re-plans after a worker by design.
    """

    def __init__(self, *, direct: frozenset[str], asking: str) -> None:
        super().__init__()
        self._direct = direct
        self._asking = asking

    @property
    def name(self) -> str:
        return type(self).__name__

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        return handler(self._narrowed(request))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        return await handler(self._narrowed(request))

    def _narrowed(self, request: Any) -> Any:
        tools = request.tools or []
        if not _reported(request.messages or [], asking=self._asking):
            return request
        kept = [tool for tool in tools if _name_of(tool) not in self._direct]
        if len(kept) == len(tools):
            return request
        logger.warning("answered_from_dispatch tools_withheld=%d", len(tools) - len(kept))
        return request.override(tools=kept)
