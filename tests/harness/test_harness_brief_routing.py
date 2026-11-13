"""D2 is routed by the decision model on the orchestrator's opening call: the fast paths (HD-7, ADR-0146).

Driven through the real `build_orchestrator` on Loom's three workers. The decision model is a fake at the capability
layer answering "does this request need the <worker>?"; one scripted chat model plays every tier and records which
tools each of its calls was offered, so a test reads whether the orchestrator made a model call before a worker ran.
"""

from __future__ import annotations

import asyncio
import dataclasses
import uuid
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from rag_wright.harness import RoutingThresholds, build_orchestrator
from rag_wright.harness.decisions import WhatTheRunDecided
from tests.harness.fabric import _a_context, _resources, _settings
from tests.harness.gold.loom import loom
from tests.harness.test_harness_routing import recording

BRIEF = "Find the cheapest mill for 10,000 m of 120 gsm poplin."
REPORT = AIMessage(content="", tool_calls=[{"name": "LoomFindings", "id": "report", "type": "tool_call",
                                            "args": {"status": "done", "need": "", "findings": ["mill-b"]}}])
COMPILED = AIMessage(content="mill-b is cheapest.")


class NeedsWorkers:
    """The decision model at the capability layer: each worker needed with the probability given."""

    def __init__(self, **yes: float) -> None:
        self.yes, self.asked = yes, []

    async def __call__(self, name: str, inputs: dict, *, resources: object = None) -> dict:
        self.asked.append(inputs)
        return {"answers": {w: {"type": "noul", "noul": self.yes.get(w, 0.0)} for w in inputs["questions"]}}


@pytest.fixture
def needs(monkeypatch: pytest.MonkeyPatch):
    import rag_wright.api

    def install(**yes: float) -> NeedsWorkers:
        fake = NeedsWorkers(**yes)
        monkeypatch.setattr(rag_wright.api, "ainvoke_model", fake)
        monkeypatch.setattr(rag_wright.api, "capability_index", lambda: {"jev_decision": {}})
        return fake
    return install


def _plan(model: Any, *, definition: Any = None) -> tuple[list, WhatTheRunDecided]:
    ledger = WhatTheRunDecided(points=())
    agent = build_orchestrator(_a_context(), _resources(), _settings(), model=model, definition=definition or loom(),
                               ledger=ledger)
    out = asyncio.run(agent.ainvoke({"messages": [{"role": "user", "content": BRIEF}]},
                                    {"configurable": {"thread_id": uuid.uuid4().hex}}, context=_a_context()))
    return out["messages"], ledger


def _d2(ledger: WhatTheRunDecided) -> list:
    (run,) = ledger.runs.values()
    return run.by_point("D2")


def test_ONE_WORKER_IS_DISPATCHED_IN_CODE_WITH_THE_BRIEF_AND_NO_PLANNING_CALL(needs) -> None:
    decision = needs(sourcing_worker=0.9, composition_worker=0.1, testing_worker=0.05)
    model, offered = recording(REPORT, COMPILED)

    _, ledger = _plan(model)

    assert "LoomFindings" in offered[0] and "task" not in offered[0], "the first model call is the worker's"
    assert len(offered) == 2, "the worker's report, then the orchestrator compiling it"
    (made,) = _d2(ledger)
    assert (made.chose, made.confidence, made.inputs["brief"]) == ("sourcing_worker", 0.9, BRIEF)
    assert set(decision.asked[0]["questions"]) == {"composition_worker", "sourcing_worker", "testing_worker"}
    assert decision.asked[0]["state"] == BRIEF


def test_NO_WORKER_MEANS_THE_OPENING_CALL_HAS_NO_HANDOFF_AND_D2_IS_NONE(needs) -> None:
    needs(sourcing_worker=0.1, composition_worker=0.2, testing_worker=0.05)
    model, offered = recording(AIMessage(content="The season plan has two collections."))

    _, ledger = _plan(model)

    assert "task" not in offered[0] and "list_mills" in offered[0], "its own tools, without the hand-off"
    (made,) = _d2(ledger)
    assert (made.chose, made.confidence) == ("none", 0.8)


def test_SEVERAL_WORKERS_ARE_PLANNED_BY_THE_ORCHESTRATOR_WITH_THE_DECISION_MODELS_VIEW_RECORDED(needs) -> None:
    needs(sourcing_worker=0.9, composition_worker=0.8, testing_worker=0.1)
    plan = AIMessage(content="", tool_calls=[{"name": "task", "id": "t1", "type": "tool_call",
                                              "args": {"description": "Find a poplin composition.",
                                                       "subagent_type": "composition_worker"}}])
    model, offered = recording(plan, REPORT, COMPILED)

    _, ledger = _plan(model)

    assert "task" in offered[0], "the orchestrator plans, with every tool"
    (made,) = _d2(ledger)
    assert (made.chose, made.confidence) == ("composition_worker", None), "the model decided, so no confidence"
    assert made.inputs["decision_model_yes"] == 0.8


def test_WITH_NO_DECISION_MODEL_THE_ORCHESTRATOR_PLANS_AS_BEFORE() -> None:
    model, offered = recording(AIMessage(content="Nothing to dispatch."))

    _, ledger = _plan(model)

    assert "task" in offered[0]
    assert _d2(ledger) == []


def test_A_PRODUCTS_OWN_D2_THRESHOLD_DECIDES_WHICH_WORKERS_ARE_NEEDED(needs) -> None:
    """0.9 is "needed" at the default 0.5; a product that asks for 0.95 needs no worker for this brief."""
    needs(sourcing_worker=0.9)
    model, offered = recording(AIMessage(content="answered with my own tools"))

    _plan(model, definition=dataclasses.replace(loom(), routing=RoutingThresholds(d2=0.95)))

    assert "task" not in offered[0]


def test_A_WORKER_THE_ORCHESTRATOR_ADDS_LATER_IS_ITS_DECISION_NOT_THE_DECISION_MODELS(needs) -> None:
    """The decision model dispatched the sourcing worker; the orchestrator then sends the testing worker itself, so
    that row carries the decision model's view as an input and no confidence."""
    needs(sourcing_worker=0.9, composition_worker=0.1, testing_worker=0.3)
    more = AIMessage(content="", tool_calls=[{"name": "task", "id": "t2", "type": "tool_call",
                                              "args": {"description": "Check the cheapest mill's poplin swatch.",
                                                       "subagent_type": "testing_worker"}}])
    model, _offered = recording(REPORT, more, REPORT, COMPILED)

    _, ledger = _plan(model)

    sourcing, testing = _d2(ledger)
    assert (sourcing.chose, sourcing.confidence) == ("sourcing_worker", 0.9)
    assert (testing.chose, testing.confidence, testing.inputs["decision_model_yes"]) == ("testing_worker", None, 0.3)
