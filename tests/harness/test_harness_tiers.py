"""The three tiers, and which operations each one holds.

Ported from RuleWright under ADR-0139's handover (PS-48g); the substrate is `rag_wright.harness.tiers`, driven
through the fabric product. RuleWright's tests of its own classification (which of its operations need planning,
and why), its operations' signatures and its prompts stay in RuleWright: those are the product's judgements.

**The direct surface is a requirement, not an optimisation.** Three tiers means three agents making model calls
before any tool runs, so routing "list the mills" through all three is latency and cost on the commonest path.
**And it is a SUBSET, never a partition**: an operation the orchestrator cannot see is a plan it cannot make. So
the chat-manager holds what needs no planning, and the orchestrator reaches all of it.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.prebuilt.tool_node import ToolRuntime

from rag_wright.harness.tiers import (
    build_orchestrator,
    chat_manager_tools,
    direct_names,
    orchestrator_direct,
    orchestrator_reachable,
    orchestrator_subagent,
    worker_operations,
)
from tests.harness.fabric import (
    FABRIC_EXPOSED,
    FABRIC_PLANNING,
    FabricContext,
    FabricToolSurface,
    _a_context,
    _fabric,
    _resources,
    _settings,
    scripted,
)


def _card() -> dict:
    return orchestrator_subagent(_a_context(), _resources(), _settings(), definition=_fabric(),
                                 model=scripted(AIMessage(content="never invoked")))


# --- the classification -------------------------------------------------------------------------------


def test_EVERY_EXPOSED_OPERATION_IS_CLASSIFIED_EXACTLY_ONCE() -> None:
    """**Derived, so two of these are true by construction**: the direct half is `exposed - planning`, so the
    union cannot miss and the halves cannot overlap. Kept as assertions: a change that stopped deriving would be
    caught here first."""
    direct = direct_names(_fabric())

    assert direct | set(FABRIC_PLANNING) == set(FABRIC_EXPOSED)
    assert not direct & set(FABRIC_PLANNING)
    assert direct and FABRIC_PLANNING, "one half is empty -- the split is not doing anything"


def test_A_PLANNING_NAME_THE_SURFACE_DOES_NOT_EXPOSE_ADDS_NOTHING_DIRECT() -> None:
    """**What a product cannot do by mistake.** A planning entry for an operation that does not exist must not
    put anything on the direct surface, nor take anything off it."""
    definition = replace(_fabric(), planning={**FABRIC_PLANNING, "fabric.no_such_operation": "x" * 50})

    assert direct_names(definition) == direct_names(_fabric())


def test_THE_ORCHESTRATOR_SEES_EVERYTHING_THE_CHAT_MANAGER_SEES() -> None:
    """**A strict subset**: a partition would mean the orchestrator could not plan work that begins with a
    listing."""
    definition = _fabric()

    assert direct_names(definition) < orchestrator_reachable(definition)
    assert orchestrator_reachable(definition) == set(FABRIC_EXPOSED)


# --- what each tier actually gets ---------------------------------------------------------------------


def test_THE_CHAT_MANAGER_CARRIES_ONLY_ITS_DIRECT_TOOLS_AND_THE_CLARIFICATION() -> None:
    """Built, not read: the tools that come back are the ones a run is really given."""
    names = {t.name for t in chat_manager_tools(_a_context(), _resources(), definition=_fabric())}
    everything = {t.name for t in FabricToolSurface().build(_a_context(), _resources())}

    assert names - {"ask_the_user"} < everything, "the chat-manager was given the whole surface"
    assert "ask_the_user" in names, "the chat-manager cannot ask a clarifying question"
    assert len(names) == len(direct_names(_fabric())) + 1


def test_EVERY_CHAT_MANAGER_TOOL_COMES_FROM_THE_PRODUCTS_SURFACE() -> None:
    """**The hazard a second tier introduces.** A product's tenancy guards read its surface; a tier that acquired
    a tool from anywhere else would hold one nothing checks. The clarification is the one named exception."""
    everything = {t.name for t in FabricToolSurface().build(_a_context(), _resources())}
    manager = {t.name for t in chat_manager_tools(_a_context(), _resources(), definition=_fabric())}

    assert manager - {"ask_the_user"} <= everything, f"unguarded: {sorted(manager - everything)}"


def test_THE_ORCHESTRATOR_IS_A_COMPILED_SUBAGENT_ON_THE_CHAT_MANAGER() -> None:
    """**Compiled, not a `SubAgent` spec**: a spec would have the parent build it, and the orchestrator's own
    middleware and workers would be the parent's to supply."""
    card = _card()

    assert set(card) == {"name", "description", "runnable"}, f"not a CompiledSubAgent: {sorted(card)}"
    assert card["name"] == "orchestrator"
    assert card["description"] == _fabric().prompts.orchestrator_description
    assert hasattr(card["runnable"], "ainvoke")


def test_THE_ORCHESTRATOR_DECLARES_THE_PRODUCTS_CONTEXT_SCHEMA() -> None:
    """Declared rather than inherited, so the tier can be driven DIRECTLY -- which `start_run` refuses
    without the schema."""
    assert _card()["runnable"].context_schema is FabricContext


@pytest.mark.asyncio
async def test_THE_SCOPE_REACHES_A_TOOL_INSIDE_THE_ORCHESTRATOR() -> None:
    """**The framework carries context across a compiled boundary; this proves the tier is wired to use it.** A
    `context_schema` omitted here would lose the scope exactly where it need not be lost."""
    seen: list = []

    @tool
    def probe(note: str, runtime: ToolRuntime[FabricContext]) -> str:
        """Record the scope this tier sees."""
        seen.append(runtime.context.scope)
        return "ok"

    context = _a_context()
    orchestrator = build_orchestrator(
        context, _resources(), _settings(), definition=_fabric(), tools=[probe],
        model=scripted(AIMessage(content="", tool_calls=[{"name": "probe", "args": {"note": "x"}, "id": "1",
                                                          "type": "tool_call"}]),
                       AIMessage(content="done")))

    await orchestrator.ainvoke({"messages": [{"role": "user", "content": "go"}]}, context=context)

    assert seen == [("acme-mills", "ss26", "resort")], f"the orchestrator's tool woke with {seen}"


# --- a tool a worker holds is not on the parent ----------------------------------------------------------


def test_THE_ORCHESTRATOR_DOES_NOT_HOLD_WHAT_ITS_WORKERS_HOLD() -> None:
    """**The rule that makes delegation worth a turn.** A parent holding its worker's tools never delegates --
    across six measured runs it did not once -- because delegating then costs a turn and saves nothing."""
    definition = _fabric()
    held = worker_operations(definition, _a_context())

    assert held == {"fabric.match_compositions"}, f"the worker holds {sorted(held)}"
    assert not orchestrator_direct(definition, _a_context()) & held


def test_THE_ORCHESTRATOR_CAN_STILL_REACH_EVERYTHING() -> None:
    """Reaching is not holding: what it does not hold, it reaches through a worker."""
    definition = _fabric()

    assert (orchestrator_direct(definition, _a_context()) | worker_operations(definition, _a_context())
            == set(FABRIC_EXPOSED)), "some operation is neither the orchestrator's nor any worker's"


def test_THE_ORCHESTRATOR_KEEPS_WHAT_A_PLAN_IS_BUILT_FROM() -> None:
    """**Anti-vacuity.** A rule that moved everything to workers would blind the planner: it could not list the
    mills in order to fan out over them."""
    direct = orchestrator_direct(_fabric(), _a_context())

    assert direct == {"fabric.list_mills"}


def test_THE_ORCHESTRATOR_IS_BOUND_ITS_DIRECT_TOOLS_AND_NOT_ITS_WORKERS() -> None:
    """Read off the compiled tier rather than the derivation, so the builder is held to it too."""
    bound = set(_card()["runnable"].nodes["tools"].bound.tools_by_name)

    assert "list_mills" in bound
    assert "match_compositions" not in bound, "the orchestrator holds its worker's tool directly"


def test_THE_CHAT_MANAGERS_SURFACE_IS_DECIDED_BY_PLANNING_NOT_BY_THE_WORKER_SPLIT() -> None:
    """**Two independent splits.** A worker taking an operation must not narrow the conversation tier: that
    would put a delegation hop in front of a listing."""
    definition = _fabric()
    planning_free = replace(definition, planning={})

    assert direct_names(definition) == set(FABRIC_EXPOSED) - set(FABRIC_PLANNING)
    assert "fabric.match_compositions" in direct_names(planning_free), (
        "the chat-manager lost an operation because a worker holds it; that split is about context, not planning"
    )
