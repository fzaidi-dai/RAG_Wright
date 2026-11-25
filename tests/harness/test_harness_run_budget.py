"""What a run spent, tier by tier, and the run-wide budget each tier is held to (PS-80).

**A product could not see a worker's turns**: `run.state` is the chat-manager's conversation, so "how many calls did
that tier take?" was answered from traces after the fact. `WhatTheRunSpent` is the run's tally, read the way the
decision ledger is.

**The per-invocation caps never bounded a run**: each dispatch counts afresh, so a run with several dispatches had no
bound on its bill. `AgentDefinition.budget` is that bound, and each tier keeps a reserve for the tiers above it, so a
worker that spends all it may still leaves the orchestrator and the chat-manager the calls to compose. A worker's own
per-invocation caps are `worker_spec(model_calls=, tool_calls=)`.

Driven through `build_chat_agent` and `start_run` with one scripted model, shared by every tier.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

import pytest
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain_core.messages import AIMessage

from rag_wright.harness import (
    RunBudget,
    WhatTheRunSpent,
    build_chat_agent,
    start_run,
    thread_for,
    worker_spec,
)
from rag_wright.harness.errors import MODEL_CALLS_PER_INVOCATION, TOOL_CALLS_PER_INVOCATION
from tests.harness.fabric import FabricFindings, FabricToolSurface, _a_context, _fabric, _resources, _settings
from tests.harness.test_harness_answered_by_dispatch import PrecedentSurface, _call, _delegate, recording

QUESTION = "Which earlier development is closest to SDF 3146?"


def _agent(*replies: Any, budget: RunBudget = RunBudget(), workers: tuple = ()) -> tuple[Any, WhatTheRunSpent, list]:
    workers = workers or (_worker,)
    definition = dataclasses.replace(
        _fabric(), surface=PrecedentSurface(), planning={"product.find_precedents": "compares across cases"},
        workers=workers, routing=None, repeatable=frozenset(), question_argument={}, budget=budget)
    model, offered = recording(*replies)
    spent = WhatTheRunSpent()
    agent = build_chat_agent(_a_context(), _resources(), _settings(), definition=definition, model=model, spent=spent)
    return agent, spent, offered


def _spend(spent: WhatTheRunSpent, run: Any) -> Any:
    return spent.of(thread_for(_a_context(), run.run_id))


def test_EACH_TIER_IS_COUNTED_UNDER_ITS_OWN_NAME() -> None:
    agent, spent, _ = _agent(_delegate(), _call("case_dossier", {"case": "3146"}, "o1"),
                             AIMessage(content="SDF 3204 is the closest."), AIMessage(content="SDF 3204."))

    run = asyncio.run(start_run(agent, _a_context(), message=QUESTION))

    tiers = _spend(spent, run).tiers
    assert (tiers["chat-manager"].invocations, tiers["chat-manager"].model_calls, tiers["chat-manager"].tool_calls) \
        == (1, 2, 1), "delegated, then answered: one `task` call"
    assert (tiers["orchestrator"].invocations, tiers["orchestrator"].model_calls, tiers["orchestrator"].tool_calls) \
        == (1, 2, 1)
    assert not any(tier.stopped_by_budget for tier in tiers.values())


def test_TOKENS_ARE_COUNTED_FROM_EACH_REPLY() -> None:
    metered = AIMessage(content="420 GSM.", usage_metadata={"input_tokens": 1200, "output_tokens": 30,
                                                           "total_tokens": 1230})
    agent, spent, _ = _agent(metered)

    run = asyncio.run(start_run(agent, _a_context(), message="What GSM did SDF 3146 finish at?"))

    chat = _spend(spent, run).of("chat-manager")
    assert (chat.input_tokens, chat.output_tokens) == (1200, 30)


def _worker(context: Any, resources: Any, ledger: Any = None) -> dict:
    return worker_spec("composition_worker", "Finds fabric compositions matching a requirement set.",
                       "You match requirement sets to fabric compositions.",
                       PrecedentSurface().build(context, resources, only=["product.find_precedents"]),
                       FabricFindings, ledger=ledger, definition=_fabric())


def test_A_WORKER_STOPS_AT_THE_BUDGET_LESS_TWO_RESERVES_AND_THE_TIERS_ABOVE_STILL_ANSWER() -> None:
    """**TexWright's first property**: a tier cannot starve the tiers above it. Seven calls with a reserve of two: the
    worker may spend while the run is under three, the orchestrator under five, the chat-manager under seven."""
    dispatch = _call("task", {"description": "Match the poplin requirement.", "subagent_type": "composition_worker"},
                     "t1")
    agent, spent, _ = _agent(
        _delegate(), dispatch, _call("match_compositions", {"brief": "poplin"}, "w1"),
        AIMessage(content="The worker stopped; nothing matched in time."), AIMessage(content="Nothing matched yet."),
        budget=RunBudget(model_calls=7, tool_calls=100, reserve=2), workers=(_worker,))

    run = asyncio.run(start_run(agent, _a_context(), message=QUESTION))

    tiers = _spend(spent, run).tiers
    assert tiers["composition_worker"].stopped_by_budget and tiers["composition_worker"].model_calls == 1
    assert not tiers["orchestrator"].stopped_by_budget and not tiers["chat-manager"].stopped_by_budget
    assert run.state["messages"][-1].content == "Nothing matched yet."


def test_A_TIER_PAST_ITS_SHARE_OF_TOOL_CALLS_IS_CALLED_WITH_NO_TOOLS() -> None:
    agent, spent, offered = _agent(*[_call("case_dossier", {"case": f"31{n}"}, f"c{n}") for n in range(5)],
                                   AIMessage(content="Five dossiers read."),
                                   budget=RunBudget(model_calls=50, tool_calls=5, reserve=2))

    run = asyncio.run(start_run(agent, _a_context(), message="Read the dossiers of 3140 to 3144."))

    assert "case_dossier" in offered[4] and offered[5] == [], offered
    assert _spend(spent, run).of("chat-manager").stopped_by_budget
    assert run.state["messages"][-1].content == "Five dossiers read."


def test_A_TIER_PAST_ITS_SHARE_OF_MODEL_CALLS_ENDS_WITH_WHAT_IT_HAS() -> None:
    agent, spent, _ = _agent(*[_call("case_dossier", {"case": f"31{n}"}, f"c{n}") for n in range(10)],
                             budget=RunBudget(model_calls=5, tool_calls=100, reserve=2))

    run = asyncio.run(start_run(agent, _a_context(), message="Read every dossier."))

    chat = _spend(spent, run).of("chat-manager")
    assert chat.model_calls == 5 and chat.stopped_by_budget
    assert "budget" in run.state["messages"][-1].content


def test_A_WORKERS_OWN_CAPS_BOUND_THAT_WORKER_ALONE() -> None:
    def caps(spec: dict) -> tuple[int, int]:
        (model,) = [m for m in spec["middleware"] if isinstance(m, ModelCallLimitMiddleware)]
        (tools,) = [m for m in spec["middleware"] if isinstance(m, ToolCallLimitMiddleware)]
        return model.run_limit, tools.run_limit

    surface = FabricToolSurface().build(_a_context(), _resources(), only=["fabric.match_compositions"])
    capped = worker_spec("composition_worker", "d", "p", surface, FabricFindings, definition=_fabric(),
                         model_calls=6, tool_calls=9)
    default = worker_spec("composition_worker", "d", "p", surface, FabricFindings, definition=_fabric())

    assert caps(capped) == (6, 9)
    assert caps(default) == (MODEL_CALLS_PER_INVOCATION, TOOL_CALLS_PER_INVOCATION)


@pytest.mark.parametrize("budget", [dict(model_calls=4, reserve=2), dict(tool_calls=3, reserve=2), dict(reserve=0)])
def test_A_BUDGET_THAT_LEAVES_A_WORKER_NOTHING_IS_REFUSED(budget: dict) -> None:
    with pytest.raises(ValueError):
        RunBudget(**budget)
