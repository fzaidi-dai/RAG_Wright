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

from dataclasses import dataclass, field
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware

from rag_wright.harness.seen import run_key

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


class OnlySoManyDispatches(AgentMiddleware):
    """Bound the number of dispatches one run may make from this tier."""

    def __init__(self, *, limit: int = DISPATCHES_PER_RUN) -> None:
        super().__init__()
        self._limit = limit
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
        budget = self._runs.get(run_key(request))
        if budget is None or budget.spent < self._limit:
            return request
        kept = [tool for tool in (getattr(request, "tools", None) or [])
                if _name_of(tool) != DISPATCH]
        if len(kept) == len(getattr(request, "tools", None) or []):
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
        return self._runs.setdefault(run_key(request), _Budget())

    def _refuse(self, request: Any, budget: _Budget) -> Any:
        if budget.spent < self._limit:
            budget.spent += 1
            return None

        from langchain_core.messages import ToolMessage

        call = request.tool_call
        already = "\n\n".join(budget.replies[-2:]) or "(the earlier reply is not in this record)"
        return ToolMessage(
            content=(
                f"You have already delegated this request {budget.spent} time(s) and it came back. "
                "Do not delegate it again -- answer the user from what you have.\n\n"
                f"What came back:\n{already}"
            ),
            tool_call_id=call.get("id", ""),
            name=DISPATCH,
        )

    def _remember(self, budget: _Budget, result: Any) -> None:
        """Keep what came back, so a refusal can hand it over instead of merely saying no."""
        content = getattr(result, "content", None)
        if content:
            budget.replies.append(str(content))


def dispatch_bound(limit: int = DISPATCHES_PER_RUN) -> OnlySoManyDispatches:
    """The bound a tier is built with."""
    return OnlySoManyDispatches(limit=limit)
