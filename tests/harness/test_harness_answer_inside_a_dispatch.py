"""A person's answer to a worker's question reaches the dispatch that asked it (PS-81).

**A worker that cannot go on asks through the orchestrator** (`BatchTheQuestions`), and that question interrupts
INSIDE the chat-manager's one dispatch. On the resume the framework runs that same `task` call again, with the same
id, to carry the answer back down. The dispatch bound counted the call when it started, so it read the replay as a
second dispatch and refused it: the person's answer went nowhere, and the run answered without it. Measured live on
the quickstart (PS-80's re-measure, traced in PS-81): the chat-manager was told "You have already delegated this
request 1 time(s) ... (the earlier reply is not in this record)" and asked the person for the fault they had just
given.

Driven through `build_chat_agent` and `start_run` / `resume_run` with one scripted model, shared by every tier.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from rag_wright.harness import build_chat_agent, resume_run, start_run
from tests.harness.fabric import FabricFindings, _a_context, _fabric, _resources, _settings
from tests.harness.test_harness_answered_by_dispatch import PrecedentSurface, _call, _delegate, recording

QUESTION = "Which earlier development is closest to SDF 3146?"
NEED = "Which season -- SS26 or AW26?"


def _worker(context: Any, resources: Any, ledger: Any = None) -> dict:
    from rag_wright.harness import worker_spec

    return worker_spec("composition_worker", "Finds fabric compositions matching a requirement set.",
                       "You match requirement sets to fabric compositions.",
                       PrecedentSurface().build(context, resources, only=["product.find_precedents"]),
                       FabricFindings, ledger=ledger, definition=_fabric())


def _asked_then_answered() -> Any:
    definition = dataclasses.replace(
        _fabric(), surface=PrecedentSurface(), planning={"product.find_precedents": "compares across cases"},
        workers=(_worker,), routing=None, repeatable=frozenset(), question_argument={})
    model, _ = recording(
        _delegate(QUESTION),
        _call("task", {"description": "Match the 3146 requirement.", "subagent_type": "composition_worker"}, "t1"),
        _call("FabricFindings", {"status": "blocked", "need": NEED}, "r1"),
        AIMessage(content="SDF 3204 is the closest in SS26."),
        AIMessage(content="SDF 3204, in SS26."))
    agent = build_chat_agent(_a_context(), _resources(), _settings(), definition=definition, model=model)
    started = asyncio.run(start_run(agent, _a_context(), message=QUESTION))
    assert started.state.get("__interrupt__"), "the worker's need never reached the person"
    return asyncio.run(resume_run(agent, _a_context(), answer="SS26", run_id=started.run_id))


def test_THE_RESUMED_DISPATCH_IS_NOT_REFUSED_AS_A_SECOND_ONE() -> None:
    run = _asked_then_answered()

    refused = [m for m in run.state["messages"] if isinstance(m, ToolMessage) and "already delegated" in str(m.content)]
    assert not refused, refused[0].content


def test_THE_ANSWER_REACHES_THE_DISPATCH_AND_THE_RUN_ANSWERS_WITH_IT() -> None:
    run = _asked_then_answered()

    (came_back,) = [m for m in run.state["messages"] if isinstance(m, ToolMessage) and m.name == "task"]
    assert "SS26" in str(came_back.content)
    assert run.state["messages"][-1].content == "SDF 3204, in SS26."
