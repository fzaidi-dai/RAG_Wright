"""Taking a tool away from one tier.

**The decision:** the interpreter is available for programmatic orchestration, but the chat-manager does
not have it.

**The obvious mechanism does not work, and knowing why is most of this module.**
`HarnessProfileConfig.excluded_tools` exists and is supported — but `_harness_profile_for_model(model,
spec)` keys a profile by the **MODEL**, and both tiers run the same `AGENT_MODEL_SPEC`. One registration
would strip `execute` from the orchestrator as well, which is exactly what must not happen. The profile
registry is the right tool for "this model cannot do X"; it cannot say "this agent cannot do X".

**A middleware can, because a middleware is attached to a tier.** `ModelRequest.tools` with
`.override(tools=…)` is public API, so the tool is never put in front of the model — and a tool in the
schema is a tool the model will try, which costs a call even when the refusal works.

**And the call is refused as well**, because filtering the current turn's schema does not cover a tool call
that arrives from a replayed history or a resumed run. Two halves of one property: the model is not
tempted, and the reach does not succeed.
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware.types import AgentMiddleware

#: What the conversation tier does not get, and why. The discipline `WITHHELD`, `PLANNING` and
#: `NON_OPERATION_TOOLS` all carry: a removal nobody wrote down is one nobody can revisit.
WITHHELD_FROM_CHAT: dict[str, str] = {
    "execute": (
        "A shell tool that CANNOT WORK here: `StateBackend` is not a `SandboxBackendProtocol`, so every "
        "call returns 'Execution not available' (measured). An advertised tool that can only "
        "fail spends a call and returns an error that reads like a temporary problem."
    ),
    "eval": (
        "The JavaScript interpreter. It belongs to programmatic orchestration and the "
        "conversation tier has nothing to compute, and one fewer thing for a model to reach "
        "for while flailing, which the routing eval showed it does."
    ),
}

#: What the PLANNING tier does not get. It keeps `eval`, which is the whole point of programmatic orchestration.
WITHHELD_FROM_ORCHESTRATOR: dict[str, str] = {
    "execute": WITHHELD_FROM_CHAT["execute"],
}


#: What a WORKER does not get. It keeps everything else the base stack offers, including the filesystem
#: tools -- recall is `glob`/`grep` over `/memories`, and the wrong turns were cured with a
#: signpost and a prompt line rather than a removal.
#:
#: **`execute` is deliberately absent from this list.** It is withheld from the two tiers above, and the
#: obvious move was to add it here for symmetry -- but measured on what the model is actually OFFERED, a
#: worker is never offered it. Adding it would be a removal with nothing behind it, and a withhold list
#: that names tools nobody has is a list nobody can trust.
#: **`write_todos` was here until `deepagents` 0.7, and it left for the reason stated just above.**
#: The framework used to force a todo list onto every agent, so keeping it off a worker meant withholding
#: the tool by hand -- justified by a live `three_ways` run where one worker made 56 calls, 35 of
#: them `write_todos`, rewriting a task list until the run hit its ceiling, while the two workers that
#: finished used it zero times. 0.7 stopped auto-adding the middleware, so a worker is no longer offered
#: the tool at all and withholding it would name a tool nobody has. The DECISION did not change and is not
#: lost: it moved to `NOT_ON_WORKERS["TodoListMiddleware"]`, where the audit guard enforces it, and the
#: orchestrator now asks for the middleware explicitly (a product's TODO panel renders its list).
WITHHELD_FROM_WORKERS: dict[str, str] = {}


def _name_of(tool: Any) -> str:
    return getattr(tool, "name", None) or (tool.get("name", "") if isinstance(tool, dict) else str(tool))


class WithheldTools(AgentMiddleware):
    """Keep named tools off one tier: out of the model's view, and refused if called anyway."""

    def __init__(self, names: frozenset[str]) -> None:
        super().__init__()
        self._names = names

    @property
    def name(self) -> str:
        """**Distinct per withheld set, because `create_agent` rejects two middlewares with one name.**

        The default is the class name, so a second `withhold(...)` on the same agent -- withholding
        something else entirely -- fails with *"Please remove duplicate middleware instances."* Two
        middlewares that take away different tools are different middlewares, and saying which makes the
        compiled graph's node names legible as well.
        """
        return f"{type(self).__name__}({','.join(sorted(self._names))})"

    def wrap_model_call(self, request, handler):
        return handler(self._without(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._without(request))

    def wrap_tool_call(self, request, handler):
        return self._refuse(request) or handler(request)

    async def awrap_tool_call(self, request, handler):
        return self._refuse(request) or await handler(request)

    def _without(self, request):
        """The turn's tools, minus the withheld ones.

        Rebuilt only when something is actually removed: `override` returns a new request, and doing it on
        every turn for no change is work the tier does not need.
        """
        kept = [tool for tool in (request.tools or []) if _name_of(tool) not in self._names]
        if len(kept) == len(request.tools or []):
            return request
        return request.override(tools=kept)

    def _refuse(self, request) -> Any:
        """**A `ToolMessage`, never a raise.** An exception would reach `ToolErrorMiddleware`, which would
        consult the taxonomy, find nothing, and halt the run — a dead session in place of a recoverable
        wrong turn. Same reasoning as `TheSubjectIsNotAFilesystem`."""
        from langchain_core.messages import ToolMessage

        call = request.tool_call
        name = call.get("name", "")
        if name not in self._names:
            return None
        return ToolMessage(
            content=(
                f"`{name}` is not available here. This tier answers with the tools it was given and "
                "delegates anything that needs more to the orchestrator."
            ),
            tool_call_id=call.get("id", ""),
            name=name,
        )


def withhold(*names: str) -> WithheldTools:
    """A middleware that keeps `names` off the tier it is attached to."""
    return WithheldTools(frozenset(names))
