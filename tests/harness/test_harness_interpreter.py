"""The orchestrator's interpreter, and nobody else's.

Ported from RuleWright under ADR-0139's handover (PS-48g); the substrate is `rag_wright.harness.interpreter`, bridged
to the fabric product's surface. RuleWright's checks that its own operations appear in the bridge stay in RuleWright.

**Two things are called "the interpreter", and only one works here.** `execute` runs a shell command in a sandbox
backend, and `StateBackend` is not one, so every call fails. `eval` runs JavaScript in-process with a host bridge
(`ptc`) that lets a program call the product's tools. **`ask_the_user` is never bridged**: resume replays the node
from the start, so a question raised inside a loop would re-run the whole program on the way back.
"""

from __future__ import annotations

from typing import Any

import pytest
from deepagents import create_deep_agent
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from rag_wright.harness.agent import build_chat_agent
from rag_wright.harness.clarify import clarification_tool
from rag_wright.harness.interpreter import NEVER_BRIDGED, PTC_BUDGET, bridged_tools, code_interpreter, js_name
from rag_wright.harness.tiers import build_orchestrator
from rag_wright.harness.withhold import WITHHELD_FROM_CHAT, WITHHELD_FROM_ORCHESTRATOR
from tests.harness.fabric import (
    FabricContext,
    FabricToolSurface,
    _a_context,
    _fabric,
    _resources,
    _settings,
    scripted,
)


class _ScopedSurface:
    """A product surface whose operation is bound to the run's scope, as a real product's is, and which also
    (wrongly) offers the clarification tool -- so the bridge's own refusal is what keeps it out."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, ...]] = []

    def build(self, context: Any, resources: Any, *, only: Any = None) -> list:
        seen = self.seen

        @tool
        def list_mills() -> dict:
            """List the mills this account can order from."""
            seen.append(context.scope)
            return {"mills": ["mill-a", "mill-b"]}

        return [list_mills, clarification_tool()]

    @property
    def exposed_names(self) -> set[str]:
        return {"fabric.list_mills"}


def _eval(program: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": "eval", "args": {"code": program}, "id": "1", "type": "tool_call"}])


async def _run_program(program: str, surface: Any) -> str:
    context = _a_context()
    agent = create_deep_agent(model=scripted(_eval(program), AIMessage(content="done")), tools=[],
                              middleware=[code_interpreter(context, _resources(), surface=surface)],
                              context_schema=FabricContext)
    state = await agent.ainvoke({"messages": [{"role": "user", "content": "go"}]}, context=context)
    return str(next(m for m in state["messages"] if getattr(m, "type", "") == "tool").content)


# --- which tier gets it -------------------------------------------------------------------------------


def test_ONLY_THE_ORCHESTRATOR_GETS_AN_INTERPRETER() -> None:
    """The conversation tier has nothing to compute; an interpreter there is one more thing to reach for while
    flailing."""
    context, model = _a_context(), scripted(AIMessage(content="x"))
    manager = build_chat_agent(context, _resources(), _settings(), definition=_fabric(), model=model)
    orchestrator = build_orchestrator(context, _resources(), _settings(), definition=_fabric(), model=model)

    assert "eval" in orchestrator.nodes["tools"].bound.tools_by_name
    assert "eval" not in manager.nodes["tools"].bound.tools_by_name


def test_THE_DEAD_SHELL_TOOL_IS_WITHHELD_FROM_BOTH_TIERS() -> None:
    """A tool that can only fail spends a call and returns an error that reads like a temporary problem."""
    assert "execute" in WITHHELD_FROM_CHAT and "execute" in WITHHELD_FROM_ORCHESTRATOR
    assert "eval" in WITHHELD_FROM_CHAT and "eval" not in WITHHELD_FROM_ORCHESTRATOR


@pytest.mark.asyncio
async def test_THE_SHELL_TOOL_REALLY_IS_DEAD_ON_THIS_BACKEND() -> None:
    """**The evidence for the line above, kept rather than described.** If a sandbox backend is ever configured this
    fails, and the withholding should be revisited rather than inherited."""
    agent = create_deep_agent(model=scripted(AIMessage(content="", tool_calls=[{
        "name": "execute", "args": {"command": "echo hi"}, "id": "1", "type": "tool_call"}]),
        AIMessage(content="done")), tools=[])
    state = await agent.ainvoke({"messages": [{"role": "user", "content": "go"}]})

    assert "not available" in str(next(m for m in state["messages"] if m.type == "tool").content).lower()


# --- what the sandbox may call -----------------------------------------------------------------------


def test_THE_BRIDGE_CARRIES_THE_PRODUCTS_WHOLE_SURFACE() -> None:
    """**A surface the model can see but the sandbox cannot is a trap**: a model wrote `tools.readTheQuestion()` for
    a tool missing from the bridge and got `TypeError: not a function`."""
    bridged = {t.name for t in bridged_tools(_a_context(), _resources(), surface=FabricToolSurface())}

    assert bridged == {t.name for t in FabricToolSurface().build(_a_context(), _resources())} - set(NEVER_BRIDGED)
    assert bridged == {"match_compositions", "list_mills"}


def test_THE_BRIDGE_NEVER_CARRIES_A_TOOL_THAT_INTERRUPTS_EVEN_IF_THE_SURFACE_OFFERS_ONE() -> None:
    """**The sharpest rule here.** Refused by the bridge itself, so a product that put the clarification on its
    surface still cannot have a program raise it."""
    bridged = {t.name for t in bridged_tools(_a_context(), _resources(), surface=_ScopedSurface())}

    assert "ask_the_user" in NEVER_BRIDGED
    assert bridged == {"list_mills"}


def test_NO_BRIDGED_TOOL_LETS_A_PROGRAM_NAME_THE_SCOPE() -> None:
    """Tenancy reaches inside the sandbox or it does not hold at all."""
    forbidden = {"account", "season", "collection", "scope", "shared_scope"}

    for bridged in bridged_tools(_a_context(), _resources(), surface=FabricToolSurface()):
        assert not forbidden & set(bridged.tool_call_schema.model_fields), f"{bridged.name} lets a program name a scope"


def test_THE_CALL_BUDGET_IS_SET_DELIBERATELY() -> None:
    """The run's own tool-call cap cannot see a call made inside the sandbox, so the bridge carries its own."""
    assert isinstance(PTC_BUDGET, int) and 0 < PTC_BUDGET <= 256


def test_THE_INTERPRETER_IS_BUILT_WITH_THE_BUDGET_AND_THE_BRIDGE() -> None:
    middleware = code_interpreter(_a_context(), _resources(), surface=FabricToolSurface())

    assert type(middleware).__name__ == "CodeInterpreterMiddleware"


# --- it actually runs ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_A_PROGRAM_CAN_CALL_A_BRIDGED_TOOL_AND_GET_ITS_RESULT_IN_THIS_RUNS_SCOPE() -> None:
    """**The whole feature, driven.** A bridge nothing has crossed is a claim, not a capability -- and the tool it
    reaches is bound to this run's scope, not another's."""
    surface = _ScopedSurface()
    result = await _run_program("const r = await tools.listMills({}); r.mills.join(',')", surface)

    assert "mill-a,mill-b" in result, f"the program could not reach a bridged tool: {result!r}"
    assert surface.seen == [("acme-mills", "ss26", "resort")], f"the bridged tool ran in {surface.seen}"


def test_THE_BRIDGE_RENAMES_TOOLS_TO_CAMEL_CASE() -> None:
    """**A namespace collision one layer down.** A model that knows a tool as `match_compositions` writes it that
    way and gets `TypeError: not a function`; whatever tells it how to call one must use these names."""
    assert js_name("match_compositions") == "matchCompositions"
    assert js_name("list_mills") == "listMills"
    assert js_name("eval") == "eval"


@pytest.mark.asyncio
async def test_THE_RENAMING_IS_THE_LIBRARYS_AND_NOT_OUR_GUESS() -> None:
    """**Derived from the sandbox itself**, so `js_name` cannot drift from what the bridge really does."""
    exposed = set((await _run_program("Object.keys(tools).join(',')", FabricToolSurface()))
                  .removeprefix("<result>").removesuffix("</result>").split(","))
    ours = {js_name(t.name) for t in bridged_tools(_a_context(), _resources(), surface=FabricToolSurface())}

    assert ours <= exposed, f"missing {sorted(ours - exposed)}; the sandbox has {sorted(exposed)}"
