"""The orchestrator plans and dispatches; it never executes a product operation (PS-84).

**A tool bound to a model is a tool the model can call**: there is no way to show a model a tool it may read but not
run. So the orchestrator was handed every operation no worker held (`orchestrator_direct`, from the 2026-09-15 rule
"an operation the orchestrator cannot see is a plan it cannot make", which took seeing for holding), and its `eval`
interpreter bridged the whole surface, the workers' operations included, so a program could bypass every worker.

What the orchestrator plans from is text: each worker's description, which `task` and the D2 routing decision read.
The engine adds to it what the worker can call, generated from the worker's own tools so it cannot drift. Every
operation that needs planning must then belong to some worker, or nothing could run it: a definition where one does
not is refused when the agent is built.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from rag_wright.harness import build_chat_agent, worker_spec
from rag_wright.harness.tiers import build_orchestrator
from tests.harness.fabric import FabricToolSurface, WorkerReport, _a_context, _fabric, _resources, _settings
from tests.harness.fabric import scripted


def _bound(**overrides: Any) -> dict[str, Any]:
    """The tools the orchestrator's model is offered, read off the compiled tier."""
    orchestrator = build_orchestrator(_a_context(), _resources(), _settings(), definition=_fabric(),
                                      model=scripted(AIMessage(content="never invoked")), **overrides)
    return orchestrator.nodes["tools"].bound.tools_by_name


def _surface_tools() -> set[str]:
    return {t.name for t in FabricToolSurface().build(_a_context(), _resources())}


# --- the orchestrator holds nothing it could execute -------------------------------------------------------


def test_THE_ORCHESTRATOR_IS_OFFERED_NO_PRODUCT_OPERATION() -> None:
    """`list_mills` is held by no worker. It used to land on the orchestrator for that reason alone."""
    bound = set(_bound())

    assert not bound & _surface_tools(), f"the orchestrator holds {sorted(bound & _surface_tools())}"


def test_THE_ORCHESTRATOR_HAS_NO_INTERPRETER() -> None:
    """A program in `eval` could call every operation on the surface, a worker's included."""
    assert "eval" not in _bound()


def test_THE_ORCHESTRATOR_KEEPS_WHAT_PLANNING_AND_DISPATCHING_NEED() -> None:
    bound = set(_bound())

    assert {"task", "write_todos", "ask_the_user"} <= bound, f"the orchestrator lost {sorted({'task', 'write_todos', 'ask_the_user'} - bound)}"


# --- it plans from what each worker can do, as text ---------------------------------------------------------


def test_A_WORKERS_DESCRIPTION_NAMES_WHAT_IT_CAN_CALL() -> None:
    @tool
    def find_similar(symptom: str) -> dict:
        """Find past tickets whose fault resembles a symptom.

        A second paragraph the planner does not need.
        """
        return {}

    spec = worker_spec("fault_worker", "Finds past faults.", "You find faults.", [find_similar], WorkerReport)

    assert spec["description"].startswith("Finds past faults.")
    assert "find_similar: Find past tickets whose fault resembles a symptom." in spec["description"]
    assert "second paragraph" not in spec["description"]


def test_THE_DISPATCH_TOOL_THE_ORCHESTRATOR_READS_CARRIES_IT() -> None:
    """Read off the orchestrator's own `task` tool, which is what its model is shown."""
    task = _bound()["task"].description

    assert "match_compositions" in task, "the orchestrator cannot see what its worker can call"


# --- every operation that needs planning has a worker to run it ---------------------------------------------


def test_A_PLANNING_OPERATION_NO_WORKER_HOLDS_IS_REFUSED() -> None:
    definition = replace(_fabric(), workers=())

    with pytest.raises(ValueError, match="fabric.match_compositions"):
        build_chat_agent(_a_context(), _resources(), _settings(), definition=definition,
                         model=scripted(AIMessage(content="never invoked")))


def test_A_PLANNING_NAME_THE_SURFACE_DOES_NOT_EXPOSE_IS_NOT_REFUSED() -> None:
    """Nothing could run it on any tier, so it asks nothing of the workers."""
    definition = replace(_fabric(), planning={**_fabric().planning, "fabric.no_such_operation": "x"})

    build_orchestrator(_a_context(), _resources(), _settings(), definition=definition,
                       model=scripted(AIMessage(content="never invoked")))
