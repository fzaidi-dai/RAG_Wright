"""What a run will actually build, before it is paid for; and the cut that makes a routing eval measure routing.

Ported from RuleWright's eval tooling under ADR-0139's handover (PS-48h), with the preflight and the stub
orchestrator, which now live in the engine as `rag_wright.harness.preflight` and take the product's definition.
RuleWright's stubs for its own operations stay in RuleWright.

**Written because a docstring was believed over the code**: a routing eval that said it measured routing and
nothing else built the real orchestrator and every worker on each delegating case.
"""

from __future__ import annotations

from dataclasses import replace
from itertools import cycle
from typing import Any

from langchain_core.messages import AIMessage

from rag_wright.harness import agent as agent_module
from rag_wright.harness.agent import build_chat_agent
from rag_wright.harness.decisions import WhatTheRunDecided
from rag_wright.harness.preflight import (
    STUB_ORCHESTRATOR_REPLY,
    only_the_chat_manager,
    report,
    stub_orchestrator,
    what_will_run,
)
from rag_wright.harness.tiers import orchestrator_subagent
from tests.harness.fabric import _a_context, _fabric, _fabric_worker, _resources, _settings, scripted


def test_THE_PREFLIGHT_REPORTS_THE_REAL_ROSTER_WHEN_NOTHING_IS_STUBBED() -> None:
    """**The anti-vacuity half, and the one that matters.** A preflight that reported "no workers" whatever the
    state would bless every eval ever written. The names are the ones the orchestrator offers."""
    plan = what_will_run(_fabric(), _a_context())

    assert plan.orchestrator == "real"
    assert plan.workers == ("composition_worker",)
    assert plan.summary == "orchestrator: real; 1 worker(s): composition_worker"


def test_THE_PREFLIGHT_READS_THE_PRODUCTS_ROSTER() -> None:
    """Two workers declared, two reported, in roster order."""
    def mill_worker(context: Any, resources: Any, ledger: Any = None) -> dict:
        return {**_fabric_worker(context, resources, ledger), "name": "mill_worker"}

    plan = what_will_run(replace(_fabric(), workers=(_fabric_worker, mill_worker)), _a_context())

    assert plan.workers == ("composition_worker", "mill_worker")


def test_THE_PREFLIGHT_SEES_A_STUBBED_ORCHESTRATOR(monkeypatch) -> None:
    """It reads the module state an eval installs, where `build_chat_agent` looks it up."""
    monkeypatch.setattr(agent_module, "orchestrator_subagent", lambda *a, **k: {"name": "orchestrator"})

    plan = what_will_run(_fabric(), _a_context())

    assert plan.orchestrator == "stubbed"
    assert plan.workers == (), f"a stubbed orchestrator builds no workers; preflight says {plan.workers}"


def test_THE_REPORT_IS_PRINTED_BEFORE_ANYTHING_IS_SPENT(capsys) -> None:
    plan = report("routing", _fabric(), _a_context())

    assert capsys.readouterr().out.strip() == f"preflight [routing]: {plan.summary}"


# --- the cut ----------------------------------------------------------------------------------------------


def test_ONLY_THE_CHAT_MANAGER_BUILDS_NOTHING_BELOW_IT() -> None:
    """**The acceptance measurement.** Running the orchestrator and every worker after the first call spends
    minutes on work no routing score reads."""
    with only_the_chat_manager(_fabric()):
        plan = what_will_run(_fabric(), _a_context())

    assert plan.orchestrator == "stubbed" and plan.workers == ()


def test_THE_CUT_IS_UNDONE_HOWEVER_THE_BLOCK_ENDS() -> None:
    """An eval that raised inside the block must not leave every later run with a stub orchestrator."""
    try:
        with only_the_chat_manager(_fabric()):
            raise RuntimeError("the eval failed")
    except RuntimeError:
        pass

    assert what_will_run(_fabric(), _a_context()).orchestrator == "real"


def test_THE_STUB_KEEPS_THE_NAME_AND_DESCRIPTION_THE_DECISION_IS_MADE_FROM() -> None:
    """**The one thing a stub must not change.** Compared against the REAL card, not the field the stub reads, so a
    rewording in place cannot pass by construction."""
    shipped = orchestrator_subagent(_a_context(), _resources(), _settings(), definition=_fabric(),
                                    model=scripted(AIMessage(content="x")))
    stub = stub_orchestrator(_a_context(), _resources(), _settings(), definition=_fabric())

    assert stub["description"] == shipped["description"]
    assert stub["name"] == shipped["name"]


def _says(brief: str, stub: dict | None = None) -> str:
    runnable = (stub or stub_orchestrator(None, None, None, definition=_fabric()))["runnable"]
    return str(runnable.invoke({"messages": [{"role": "user", "content": brief}]})["messages"][-1].content)


def test_THE_STUB_ANSWERS_THE_BRIEF_IT_WAS_GIVEN() -> None:
    """**A canned answer sends the chat-manager round again**: measured, three re-delegations in a row because the
    reply never addressed the question."""
    assert "every organic twill in the resort collection" in _says("every organic twill in the resort collection")


def test_THE_STUB_ANSWERS_EVERY_DISPATCH_NOT_JUST_THE_FIRST() -> None:
    """A scripted model exhausts its iterator; a stub that ran dry on the second dispatch would fail a case for a
    reason invented here."""
    stub = stub_orchestrator(None, None, None, definition=_fabric())

    for attempt in range(3):
        assert _says(f"go {attempt}", stub).strip(), f"the stub ran dry on dispatch {attempt + 1}"


def test_THE_STUB_READS_AS_FINISHED_WORK_NOT_AS_AN_ACKNOWLEDGEMENT() -> None:
    """"I will look into it" is the shape that invites a re-dispatch."""
    said = _says("which mills stock poplin").lower()

    assert "complete" in said or "done" in said
    assert len(said) > 60, "too terse to answer from"


def test_A_PRODUCT_CAN_SAY_ITS_OWN_REPLY() -> None:
    """The reply is the product's to word, as long as it names the brief."""
    stub = stub_orchestrator(None, None, None, definition=_fabric(), reply="Done: {brief}. Two mills stock it.")

    assert _says("poplin", stub) == "Done: poplin. Two mills stock it."


def test_THE_CHAT_MANAGER_STILL_DELEGATES_AND_GETS_AN_ANSWER_BACK() -> None:
    """**The cut, end to end, with no provider involved.** A cut that removed the workers and also broke delegation
    would satisfy every other test here. The stub records into the run's ledger, so the routing decision is
    visible."""
    delegates = AIMessage(content="", tool_calls=[{
        "name": "task", "id": "a", "type": "tool_call",
        "args": {"description": "which mills stock poplin", "subagent_type": "orchestrator"}}])
    model = scripted()
    object.__setattr__(model, "messages", cycle([delegates, AIMessage(content="Two mills stock poplin.")]))
    ledger, context = WhatTheRunDecided(), _a_context()

    with only_the_chat_manager(_fabric()):
        agent = build_chat_agent(context, _resources(), _settings(), model=model, ledger=ledger, definition=_fabric())
        state = agent.invoke({"messages": [{"role": "user", "content": "which mills stock poplin?"}]},
                             context=context, config={"configurable": {"thread_id": "cut-1"}})

    said = [m for m in state["messages"] if getattr(m, "name", "") == "task"]
    assert said and STUB_ORCHESTRATOR_REPLY[:30] in str(said[-1].content), "the stub's answer did not come back"
    assert [d.chose for d in ledger.of("cut-1").by_point("D1")] == ["delegate"], "D1 was not recorded"
