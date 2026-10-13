"""PS-32: the harness reaches the engine through `rag_wright.api` only, and each of the three seams that replaced a
product import behaves: one generation emitter per call, the engine's query encoder built once per process (and
switchable off), and a run's generations grouped under its run id."""
from __future__ import annotations

import pytest

from rag_wright import api
from rag_wright.harness import models, seen, tracing


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")


def test_the_harness_model_is_metered_by_the_engine_and_traced_only_by_the_harness():
    from rag_wright.models.seam import _AgentCallMeter

    model = models.agent_chat_model(None, "qwen3.8-27b-modal-or")
    meters = [c for c in model.callbacks if isinstance(c, _AgentCallMeter)]
    assert len(meters) == 1 and meters[0].trace is False  # GenerationRecorder is the one emitter
    assert model.max_retries == models.MODEL_RETRIES and model.request_timeout == models.MODEL_REQUEST_TIMEOUT_S


def test_a_generation_opens_and_closes_through_the_engine(monkeypatch):
    calls = []
    monkeypatch.setattr(api, "tracing_on", lambda: True)
    monkeypatch.setattr(api, "trace_level", lambda: "generations")

    class Gen:
        def update(self, **kw):
            calls.append(("update", kw))

    monkeypatch.setattr(api, "start_generation", lambda **kw: calls.append(("start", kw)) or Gen())
    monkeypatch.setattr(api, "finish_generation", lambda gen, **kw: calls.append(("finish", kw)))
    monkeypatch.setattr(api, "flush_tracing", lambda: calls.append(("flush", {})))
    gen = tracing.open_generation(None, model=None, input=["hi"])
    tracing.record_generation(None, model="served/model", usage={"input": 3, "output": 2}, cost=0.001,
                              latency_ms=12.0, metadata={"cost_basis": "per-call"}, generation=gen)
    kinds = [k for k, _ in calls]
    assert kinds == ["start", "update", "finish"]  # no flush below verbose
    assert calls[0][1]["label"] == "agent-llm" and calls[1][1] == {"model": "served/model"}
    assert calls[2][1]["cost"] == 0.001 and calls[2][1]["metadata"] == {"cost_basis": "per-call"}


def test_tracing_off_opens_nothing(monkeypatch):
    monkeypatch.setattr(api, "tracing_on", lambda: False)
    monkeypatch.setattr(api, "start_generation", lambda **kw: pytest.fail("traced while off"))
    assert tracing.open_generation(None, model="m") is None
    tracing.record_generation(None, model="m", output="x")


def test_a_run_is_grouped_under_its_run_id(monkeypatch):
    from contextlib import contextmanager

    seen_kw = {}

    @contextmanager
    def fake_traced_run(**kw):
        seen_kw.update(kw)
        yield

    monkeypatch.setattr(api, "traced_run", fake_traced_run)
    with tracing.traced_agent_run(None, session_id="run-1", name="agent-run", metadata={"k": "v"}):
        pass
    assert seen_kw == {"job_id": "run-1", "name": "agent-run", "metadata": {"k": "v"}}


def test_the_question_encoder_is_the_engines_built_once(monkeypatch):
    built = []
    monkeypatch.setattr(seen, "_QUESTION_ENCODER", [])
    monkeypatch.delenv("RAG_QUESTION_ENCODER", raising=False)
    monkeypatch.setattr(api, "query_embedder", lambda: built.append(1) or "ENC")
    assert seen.question_encoder() == "ENC" and seen.question_encoder() == "ENC"
    assert built == [1]


def test_the_question_encoder_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(seen, "_QUESTION_ENCODER", [])
    monkeypatch.setenv("RAG_QUESTION_ENCODER", "off")
    monkeypatch.setattr(api, "query_embedder", lambda: pytest.fail("built although switched off"))
    assert seen.question_encoder() is None


def test_an_encoder_that_cannot_be_built_is_none_not_a_failed_run(monkeypatch):
    monkeypatch.setattr(seen, "_QUESTION_ENCODER", [])
    monkeypatch.delenv("RAG_QUESTION_ENCODER", raising=False)

    def boom():
        raise RuntimeError("no backend")

    monkeypatch.setattr(api, "query_embedder", boom)
    assert seen.question_encoder() is None
