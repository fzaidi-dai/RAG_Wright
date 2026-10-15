"""A changed view is confirmed, never silently adopted .

**The failure being prevented is a frequent shape**: *"Silently
switching subject produces a confident answer about the wrong object."* The user runs sweep #1, navigates to
sweep #2, types "what about this one?", and every word of the answer is about the wrong sweep — fluent,
cited, and wrong about which object it is describing.

**Deterministic, not prompt-directed.** Asking the model to notice that it has been handed a different
subject is asking the failure to detect itself. The comparison happens in `before_agent`, before the model
runs at all, against the view the thread last confirmed — which lives in the checkpoint, so it survives the
process that started the thread.

**`before_agent` REPLAYS on resume, exactly as a tool does** (verified). So the replay rule binds
here too. The difference from `clarify.py` is that this node's pre-interrupt half **cannot** be empty:
deciding whether to ask requires reading both views. So the rule is expressed as *nothing awaited* — every side
effect in this codebase is awaited — plus a small maintained allow-list of the synchronous calls, because
a logger or a counter would slip past an `await` guard.

**Rejected alternative: making the answer structured.** A two-button "yes/no" resume would let the middleware
act deterministically on the decision. It would also mean the confirmation could only ever be asked by a
client that knows about the affordance. Sending the human's own words back into the conversation, framed,
works for any client and puts the decision in front of the model as a recorded fact. The limitation is
honest and stated: the model acts on the confirmation, the runtime does not roll anything back.
"""

from __future__ import annotations

from typing import Any, NotRequired

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langgraph.types import interrupt

#: The synchronous calls permitted before the `interrupt()`. Each is safe to run twice, which is the only
#: question that matters here: `get` reads state, `str` converts, `confirmation_question` formats a string.
#:
#: **Maintained, and that is the point.** Forbidding every call would forbid `state.get`, without which the
#: decision cannot be made at all. The `await` guard beside this one catches everything that costs money or
#: writes a row; this one catches the synchronous side effect -- a logger, a counter, a cache write -- that
#: would otherwise slip past it.
PURE_BEFORE_THE_INTERRUPT = {"get", "str", "confirmation_question"}


class ViewingState(AgentState):
    """The two views the comparison needs.

    `viewing` arrives with the turn (`start_run` puts it there); `confirmed_view` is what the thread has
    agreed it is working on, and is written only after a confirmation.
    """

    viewing: NotRequired[dict[str, Any] | None]
    confirmed_view: NotRequired[dict[str, Any] | None]


def confirmation_question(surface: str) -> str:
    """One line, as the note specifies — naming what moved, so the user need not re-read the screen."""
    return f"You have moved to a different {surface}. Do you mean this one?"


class ConfirmTheView(AgentMiddleware):
    """Stop the run when the subject has moved, and let a person say which one they meant."""

    state_schema = ViewingState

    def before_agent(self, state, runtime) -> dict[str, Any] | None:
        arriving = state.get("viewing")
        confirmed = state.get("confirmed_view")

        # **Absence means unchanged** (the note's decision 1: the client sends the continuation when it
        # CHANGES). Treating a missing view as a move would confirm on every ordinary turn.
        if arriving is None:
            return None

        # Nothing to have moved from. Asking on the first view would make opening chat cost a turn.
        if confirmed is None or arriving.get("subject") == confirmed.get("subject"):
            return {"confirmed_view": arriving}

        question = confirmation_question(str(arriving.get("surface", "view")))

        # **Everything above this line is a pure read**, and everything below happens once. Resume replays
        # this node from the top: an adoption written before the interrupt would confirm the move against
        # itself, and the question would never be asked again.
        answer = interrupt({"question": question, "kind": "confirm_view",
                            "surface": arriving.get("surface")})

        return {
            "confirmed_view": arriving,
            # Framed, so the model reads a recorded decision rather than something indistinguishable from
            # the user's own next sentence -- the same reason `<agent_memory>` and `<viewing>` are tagged.
            "messages": [{"role": "user",
                          "content": f"<view_confirmation>\nAsked: {question}\n"
                                     f"They answered: {answer}\n</view_confirmation>"}],
        }
