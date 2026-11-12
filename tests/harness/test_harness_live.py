"""PS-32 live: a chat run on the engine's harness tier, with an interrupt and a resume, on a real model.

The product is the conformance test's fabric product (a three-part scope, its own tools, prompts and retry set), so
the run proves the tier drives a product that is not the one it was separated from. The model comes from an engine
profile id; the checkpointer and the memory store are the caller's. A handful of cheap OpenRouter calls.
"""
from __future__ import annotations

import dataclasses
import uuid
from types import SimpleNamespace

import pytest

from rag_wright.api import measure_usage
from rag_wright.harness import RunOutcome, build_chat_agent, read_run, resume_run, start_run
from tests.harness.test_greenfield_conformance import _a_context, _fabric, _resources

pytestmark = pytest.mark.model

MODEL = "qwen3.8-27b-modal-or"


async def test_a_chat_run_waits_on_a_person_and_resumes_to_an_answer():
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.store.memory import InMemoryStore

    definition = dataclasses.replace(_fabric(), model_id_for=lambda settings: MODEL)
    context = _a_context()
    agent = build_chat_agent(context, _resources(), SimpleNamespace(), definition=definition,
                             checkpointer=InMemorySaver(), store=InMemoryStore())

    with measure_usage() as usage:
        first = await start_run(
            agent, context, run_id=str(uuid.uuid4()),
            message=("Before doing anything else, call ask_the_user to ask me which season I mean. "
                     "Do not call any other tool and do not answer yet."))
        # The outcome is read back from the checkpoint, the way a product's view reads a run it did not start.
        waiting = await read_run(agent, context, run_id=first.run_id)
        assert waiting.outcome == RunOutcome.WAITING_ON_A_PERSON, waiting.outcome

        second = await resume_run(agent, context, run_id=first.run_id,
                                  answer="Spring/summer 2026. Reply with one short sentence confirming it.")
    done = await read_run(agent, context, run_id=first.run_id)
    assert done.outcome == RunOutcome.COMPLETE, done.outcome
    assert second.run_id == first.run_id
    assert usage.calls >= 2  # every turn went through the engine's metered model
    print(f"\nlive harness run: {usage.calls} calls, ${usage.cost_usd:.5f}")


async def test_OPENROUTER_reports_a_REAL_cost_and_the_recorder_finds_it():
    """**The one assertion no fake can make** (ported from RuleWright under PS-48g). LangChain drops OpenRouter's
    cost on the streaming path, so the recorder reads it back itself -- but only if `usage: {include: true}` reached
    the provider and the field is where `real_cost_of` looks. If this fails, agent runs silently price from
    Langfuse's table instead. One call, one word."""
    from rag_wright.harness.models import GenerationRecorder, agent_chat_model
    from rag_wright.pack_sdk import profile_for

    if profile_for(MODEL).backend != "openrouter":
        pytest.skip("not OpenRouter: a per-second-billed deployment reports no per-call cost, by design")

    recorder = GenerationRecorder(SimpleNamespace(), MODEL)
    answer = await agent_chat_model(SimpleNamespace(), MODEL).ainvoke(
        "Reply with the single word: ready.", config={"callbacks": [recorder]})

    assert answer.content, "the model returned nothing, so there is no call to price"
    assert recorder.costs and recorder.costs[0] > 0.0, (
        "OpenRouter reported no per-call cost: `usage: {include: true}` is not reaching the provider, or the "
        "field moved"
    )
    print(f"\nlive cost read back: ${recorder.costs[0]:.6f}")
