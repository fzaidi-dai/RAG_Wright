"""The orchestrator's dispatches to workers are bounded per run (PS-86).

**Measured by TexWright**: one question, one orchestrator invocation, one worker dispatched **17 times** -- 58 model
calls and 528k input tokens for a list of assumptions. The chat-manager was bounded (`DISPATCHES_PER_RUN`); the
orchestrator was not, and nothing on `AgentDefinition` reached its middleware. `RunBudget` bounds the run's calls, not
its dispatches: that run stopped two calls short of it.

**A planner may dispatch several workers for one question**, so the bound is the product's, `RunBudget.dispatches`, not
the chat-manager's one. Past it a dispatch never runs, and the tier is told to report from what came back rather than
handed a cap message as its answer. What was dispatched, and what was refused, is in the run's tally.

Driven through `build_chat_agent` and `start_run` with one scripted model, shared by every tier.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import inspect
import textwrap
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from rag_wright.harness import RunBudget, WhatTheRunSpent, build_chat_agent, resume_run, start_run, thread_for
from rag_wright.harness import tiers as tiers_module
from rag_wright.harness.dispatch import DISPATCHES_PER_RUN
from tests.harness.fabric import _a_context, _fabric, _resources, _settings
from tests.harness.test_harness_answered_by_dispatch import PrecedentSurface, _call, _delegate, recording
from tests.harness.test_harness_run_budget import _worker

QUESTION = "Which earlier development is closest to SDF 3146?"
WORKER = "composition_worker"


def _dispatch(n: int) -> AIMessage:
    return _call("task", {"description": f"Match requirement {n}.", "subagent_type": WORKER}, f"t{n}")


def _report(n: int) -> AIMessage:
    return _call("FabricFindings", {"status": "complete", "need": "", "swatches": [f"swatch-{n}"]}, f"r{n}")


def _agent(*replies: Any, budget: RunBudget) -> tuple[Any, WhatTheRunSpent, list]:
    definition = dataclasses.replace(
        _fabric(), surface=PrecedentSurface(), planning={"product.find_precedents": "compares across cases"},
        workers=(_worker,), routing=None, repeatable=frozenset(), question_argument={}, budget=budget)
    model, offered = recording(*replies)
    spent = WhatTheRunSpent()
    agent = build_chat_agent(_a_context(), _resources(), _settings(), definition=definition, model=model, spent=spent)
    return agent, spent, offered


def _fan_out(times: int, *, dispatches: int) -> tuple[Any, Any, list]:
    """The chat-manager delegates once; the orchestrator dispatches the worker `times` times, one per turn, each
    dispatch that runs reported at once; then the orchestrator reports and the chat-manager answers."""
    replies: list[Any] = [_delegate()]
    for n in range(times):
        replies.append(_dispatch(n))
        if n < dispatches:
            replies.append(_report(n))
    replies += [AIMessage(content="Two swatches match."), AIMessage(content="Two swatches match.")]
    agent, spent, offered = _agent(*replies, budget=RunBudget(dispatches=dispatches))
    run = asyncio.run(start_run(agent, _a_context(), message=QUESTION))
    return run, spent.of(thread_for(_a_context(), run.run_id)), offered


def _orchestrator_turns(offered: list) -> list[list[str]]:
    """The orchestrator's turns among every tier's: only it has a todo list and `grep` (the chat-manager's is withheld,
    a worker has no todo list); spent, it has no tools at all."""
    return [names for names in offered if {"write_todos", "grep"} <= set(names) or names == []]


# --- the bound ---------------------------------------------------------------------------------------------


def test_THE_ORCHESTRATORS_DISPATCHES_PAST_THE_BUDGET_NEVER_RUN() -> None:
    run, spend, _ = _fan_out(3, dispatches=2)

    assert spend.of(WORKER).invocations == 2, "a dispatch past the bound reached the worker"
    assert run.state["messages"][-1].content == "Two swatches match.", "the run did not compose an answer"


def test_WITHIN_THE_BUDGET_EVERY_DISPATCH_RUNS() -> None:
    """Anti-vacuity: a planner dispatching several workers for one question is intended."""
    _, spend, _ = _fan_out(3, dispatches=3)

    assert spend.of(WORKER).invocations == 3


def test_ONCE_SPENT_THE_ORCHESTRATOR_IS_NOT_OFFERED_TASK() -> None:
    """The tool leaves the turn as well as being refused: a tool in the schema is one the model will try."""
    _, _, offered = _fan_out(2, dispatches=2)

    orchestrator_turns = _orchestrator_turns(offered)
    assert "task" in orchestrator_turns[0], orchestrator_turns
    assert "task" not in orchestrator_turns[-1], orchestrator_turns


def test_THE_REFUSAL_TELLS_THE_ORCHESTRATOR_TO_REPORT_NOT_TO_ANSWER_THE_USER() -> None:
    """The orchestrator never talks to the person: its refusal says to report from what the workers returned."""
    from rag_wright.harness.dispatch import OnlySoManyDispatches, _Budget

    bound = OnlySoManyDispatches(limit=1, tier="orchestrator")
    call = {"name": "task", "args": {"description": "x", "subagent_type": WORKER}, "id": "late", "type": "tool_call"}
    refusal = bound._refuse(type("R", (), {"tool_call": call})(), _Budget(spent=1))

    assert "report" in refusal.content.lower() and "the user" not in refusal.content.lower(), refusal.content


# --- what was dispatched is readable ----------------------------------------------------------------------


def test_THE_TALLY_NAMES_THE_DISPATCHES_RUN_AND_REFUSED() -> None:
    _, spend, _ = _fan_out(3, dispatches=2)

    assert spend.of("orchestrator").dispatched == [WORKER, WORKER]
    assert spend.of("orchestrator").refused == [WORKER]
    assert spend.of("chat-manager").dispatched == ["orchestrator"] and spend.of("chat-manager").refused == []


# --- the definition and the wiring ------------------------------------------------------------------------


def test_THE_DEFAULT_IS_SIX_AND_THE_CHAT_MANAGER_STAYS_AT_ONE() -> None:
    assert RunBudget().dispatches == 6
    assert DISPATCHES_PER_RUN == 1


@pytest.mark.parametrize("dispatches", [0, -1])
def test_A_BUDGET_WITH_NO_DISPATCH_IS_REFUSED(dispatches: int) -> None:
    with pytest.raises(ValueError, match="dispatches"):
        RunBudget(dispatches=dispatches)


def test_THE_ORCHESTRATOR_CARRIES_THE_BOUND() -> None:
    """**Where a bound like this usually dies**: the builder line deleted, every behaviour test above would fail,
    but this names where."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(tiers_module.build_orchestrator)))

    assert "dispatch_bound" in {n.func.id for n in ast.walk(tree)
                                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}


def test_A_CALL_REFUSED_BESIDE_ONE_THAT_RAN_IS_TOLD_ITS_REPLY_IS_COMING() -> None:
    """**Measured live**: eight `task` calls in one turn, one ran and seven were told "the earlier reply is not in
    this record" -- true when they were refused, as the one that ran had not returned -- and the tier, reading that it
    had nothing, went looking on the filesystem ten times. Its reply arrives in the same turn."""
    from rag_wright.harness.dispatch import OnlySoManyDispatches, _Budget

    call = {"name": "task", "args": {"description": "x", "subagent_type": WORKER}, "id": "sibling", "type": "tool_call"}
    refusal = OnlySoManyDispatches(limit=1)._refuse(type("R", (), {"tool_call": call})(), _Budget(spent=1))

    assert "same turn" in refusal.content and "not in this record" not in refusal.content, refusal.content




def test_ONCE_SPENT_ON_A_FINISHED_WORKER_THE_ORCHESTRATOR_HAS_NO_TOOLS_SO_IT_REPORTS() -> None:
    """**Measured live, with `task` merely taken away**: two dispatches, then 22 more model calls and 14 tool calls on
    the filesystem and the todo list until the run's budget stopped the tier -- whose report was then the cap message,
    and the run's answer with it. **And with `ask_the_user` kept**, twice of two, the spent orchestrator asked the
    person whether to dispatch more, which it could not. A worker's question does not need the tool: `BatchTheQuestions`
    asks it in code. So a spent orchestrator has one thing left, its report."""
    replies = [_delegate(), _dispatch(0), _report(0), AIMessage(content="The report."), AIMessage(content="The answer.")]
    agent, _, offered = _agent(*replies, budget=RunBudget(dispatches=1))

    run = asyncio.run(start_run(agent, _a_context(), message=QUESTION))

    assert _orchestrator_turns(offered)[-1] == [], offered
    assert run.state["messages"][-1].content == "The answer."


def test_A_PERSONS_ANSWER_TO_A_WORKERS_QUESTION_MAY_BE_CARRIED_DOWN_PAST_THE_BOUND() -> None:
    """**An answer is new intent** (PS-80), whichever way it arrives. A worker's need is asked by `BatchTheQuestions`
    and comes back as a framed message, not an `ask_the_user` result, so it did not count as one: at the bound the
    orchestrator could hear the person's answer and never take it to the worker that needed it."""
    blocked = _call("FabricFindings", {"status": "blocked", "need": "Which mill?", "swatches": []}, "r0")
    replies = [_delegate(), _dispatch(0), blocked, AIMessage(content="Mill A has it."), AIMessage(content="Mill A.")]
    agent, _, offered = _agent(*replies, budget=RunBudget(dispatches=1))

    run = asyncio.run(start_run(agent, _a_context(), message=QUESTION))
    assert run.waiting_on is not None and run.waiting_on.kind == "batched_needs", run.waiting_on
    asyncio.run(resume_run(agent, _a_context(), answer="Mill A.", run_id=run.run_id))

    assert "task" in _orchestrator_turns(offered)[-1], offered


def test_THE_SPENT_ORCHESTRATOR_IS_TOLD_WHY_IT_HAS_NO_TOOLS() -> None:
    """**Measured live, with the tools taken away and nothing said**: twice of two, the spent orchestrator's reply was
    its plan ("I'll dispatch one `fault_worker` per symptom...") rather than a report, which the chat-manager could not
    answer from. A tier told only "no" flails; it is told it has dispatched all it may, and to report."""
    from types import SimpleNamespace

    from langchain_core.messages import HumanMessage

    from rag_wright.harness.dispatch import OnlySoManyDispatches, _asked, _Budget

    captured: dict = {}
    request = SimpleNamespace(
        messages=[HumanMessage(content="Match the poplin.")], tools=[{"name": "task"}, {"name": "ls"}],
        runtime=SimpleNamespace(config={"configurable": {"thread_id": "run-1"}}),
        override=lambda **changes: captured.update(changes) or request)
    bound = OnlySoManyDispatches(limit=2, tier="orchestrator")
    bound._runs[_asked(request, request.messages)] = _Budget(spent=2)

    bound._without_the_dispatch(request)

    assert captured["tools"] == []
    note = captured["messages"][-1]
    assert isinstance(note, HumanMessage) and "2 worker" in note.content and "report" in note.content, note
    assert captured["messages"][:-1] == request.messages, "the note is added, nothing else changes"
