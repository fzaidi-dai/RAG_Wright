"""A person's answer to a clarification is routed (PS-80).

**`ask_the_user` returns the answer as its tool result**, so on a resume the chat-manager's call opens on a tool message
and `RouteTheTurn`, which routed only a human one, never saw it. TexWright measured the cost: the resumed turn was the
chat-manager's alone, 20 model calls and 571k input tokens against 4 and 54k, for an answer that had narrowed the task.

Now the first call after an answer is routed as an opening is, on what was asked, the question and the answer together;
the run's one dispatch is counted per thing the person asked, so a resumed turn can delegate though the run delegated
before it asked; and the ledger records the resumed route as a second D1.

Driven through `build_chat_agent` and `start_run` / `resume_run`, with the decision model faked at the capability
layer and one scripted model, shared by both tiers, that records what each call was offered.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from rag_wright.harness import RoutingThresholds, build_chat_agent, resume_run, start_run
from rag_wright.harness.decisions import WhatTheRunDecided
from tests.harness.fabric import _a_context, _fabric, _resources, _settings
from tests.harness.test_harness_answered_by_dispatch import ASK, PrecedentSurface, _call, precedent_worker, recording

QUESTION = "Which earlier development is closest to SDF 3146?"
ASKED = "SS26 or AW26?"
WHAT_WAS_ASKED = f"{QUESTION}\n\nAsked: {ASKED}\nAnswered: SS26"


class Routes:
    """The decision model: D1 answers `choices` in order, each at 0.9; B1 says every brief stands on its own; D2 needs
    no worker."""

    def __init__(self, *choices: str) -> None:
        self.choices, self.states = list(choices), []

    async def __call__(self, name: str, inputs: dict, *, resources: object = None) -> dict:
        if "self_contained" in inputs["questions"]:
            return {"answers": {"self_contained": {"type": "noul", "noul": 0.9}}}
        if "precedent_worker" in inputs["questions"]:  # D2: the orchestrator's opening call keeps the brief
            return {"answers": {"precedent_worker": {"type": "noul", "noul": 0.0}}}
        self.states.append(inputs["state"])
        choice = self.choices.pop(0)
        return {"answers": {"choice": {"type": "choice", "choice": choice, "probabilities": {choice: 0.9}}}}


@pytest.fixture
def routes(monkeypatch: pytest.MonkeyPatch):
    import rag_wright.api

    def install(*choices: str) -> Routes:
        fake = Routes(*choices)
        monkeypatch.setattr(rag_wright.api, "ainvoke_model", fake)
        monkeypatch.setattr(rag_wright.api, "capability_index", lambda: {"jev_decision": {}})
        return fake
    return install


def _agent(*replies: Any, ledger: WhatTheRunDecided | None = None) -> tuple[Any, PrecedentSurface, list[list[str]]]:
    surface = PrecedentSurface()
    definition = dataclasses.replace(
        _fabric(), surface=surface, planning={"product.find_precedents": "compares across cases"},
        workers=(precedent_worker,),
        routing=RoutingThresholds(), repeatable=frozenset(), question_argument={})
    model, offered = recording(*replies)
    agent = build_chat_agent(_a_context(), _resources(), _settings(), definition=definition, model=model,
                             ledger=ledger)
    return agent, surface, offered


def _asked_then_answered(agent: Any) -> Any:
    started = asyncio.run(start_run(agent, _a_context(), message=QUESTION))
    return asyncio.run(resume_run(agent, _a_context(), answer="SS26", run_id=started.run_id))


def test_THE_FIRST_CALL_AFTER_AN_ANSWER_IS_ROUTED_ON_WHAT_WAS_ASKED(routes) -> None:
    decision = routes(ASK, "case_dossier")
    agent, surface, offered = _agent(_call(ASK, {"question": ASKED}, "q1"),
                                     _call("case_dossier", {"case": "3146-ss26"}, "c1"), AIMessage(content="420 GSM."))

    run = _asked_then_answered(agent)

    assert decision.states == [QUESTION, WHAT_WAS_ASKED]
    assert offered[1] == ["case_dossier"], "the routed call after the answer is offered the chosen tool alone"
    assert surface.ran == ["case_dossier"]
    assert run.state["messages"][-1].content == "420 GSM."


def test_ONLY_THAT_ONE_CALL_IS_ROUTED(routes) -> None:
    """**Not a thrash**: the call after the routed one opens on a tool result of its own, and is under way."""
    decision = routes(ASK, "case_dossier")
    agent, _, offered = _agent(_call(ASK, {"question": ASKED}, "q1"),
                               _call("case_dossier", {"case": "3146-ss26"}, "c1"), AIMessage(content="420 GSM."))

    _asked_then_answered(agent)

    assert len(decision.states) == 2
    assert "case_dossier" in offered[2] and ASK in offered[2], "the rest of the turn has every tool again"


def test_A_RESUMED_TURN_CAN_DELEGATE_THOUGH_THE_RUN_DELEGATED_BEFORE_IT_ASKED(routes) -> None:
    """**An answer is new intent**, so the one dispatch is per thing asked: the orchestrator came back blocked, the
    person answered, and the answer goes to the orchestrator as what was asked."""
    routes("delegate", "delegate")
    blocked = AIMessage(content=f"Blocked: {ASKED}")
    agent, _, _ = _agent(blocked, _call(ASK, {"question": ASKED}, "q1"),
                         AIMessage(content="SDF 3204 is the closest in SS26."), AIMessage(content="SDF 3204."))

    run = _asked_then_answered(agent)

    briefs = [call["args"]["description"] for m in run.state["messages"] if isinstance(m, AIMessage)
              for call in m.tool_calls if call["name"] == "task"]
    assert briefs == [QUESTION, WHAT_WAS_ASKED]
    refused = [m for m in run.state["messages"] if isinstance(m, ToolMessage) and "already delegated" in str(m.content)]
    assert not refused, refused
    assert run.state["messages"][-1].content == "SDF 3204."


def test_THE_LEDGER_RECORDS_THE_RESUMED_ROUTE_AS_A_SECOND_D1(routes) -> None:
    routes("delegate", "delegate")
    ledger = WhatTheRunDecided(points=())
    agent, _, _ = _agent(AIMessage(content=f"Blocked: {ASKED}"), _call(ASK, {"question": ASKED}, "q1"),
                         AIMessage(content="SDF 3204 is the closest in SS26."), AIMessage(content="SDF 3204."),
                         ledger=ledger)

    _asked_then_answered(agent)

    (run,) = ledger.runs.values()
    first, again = run.by_point("D1")
    assert first.chose == again.chose == "delegate"
    assert "resumed" not in first.inputs and again.inputs["resumed"] is True
    assert again.inputs["brief"] == WHAT_WAS_ASKED


def test_A_TOOL_RESULT_THAT_IS_NOT_AN_ANSWER_IS_NOT_ROUTED(routes) -> None:
    """**The turn already under way stays under way**: a direct tool's result is not the person speaking."""
    decision = routes("case_dossier")
    agent, _, _ = _agent(_call("case_dossier", {"case": "3146"}, "c1"), AIMessage(content="420 GSM."))

    asyncio.run(start_run(agent, _a_context(), message=QUESTION))

    assert decision.states == [QUESTION]
