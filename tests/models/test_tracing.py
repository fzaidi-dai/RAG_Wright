"""Engine issue 0017/0048: LLM tracing gate + emission. Hermetic -- a fake langfuse client, no network.

0048: a generation is opened BEFORE the call (`start_generation`) and ended AFTER (`finish_generation`), so
the Langfuse span's own duration is the real latency. langfuse v4 cannot back-date a start, so the old
post-hoc emit read ~0s; these tests assert the open-before/close-after shape."""

from __future__ import annotations

from rag_wright.models import tracing


class _FakeGen:
    def __init__(self, rec):
        self._rec = rec

    def update(self, **kw):
        self._rec["update"] = kw
        return self

    def end(self):
        self._rec["ended"] = True


class _FakeLF:
    def __init__(self):
        self.calls = []

    def start_observation(self, **kw):
        rec = {"start": kw}
        self.calls.append(rec)
        return _FakeGen(rec)

    def flush(self):
        self.calls.append({"flush": True})


def _creds(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")


def test_trace_level_defaults_off(monkeypatch):
    monkeypatch.delenv("RAG_TRACE_LEVEL", raising=False)
    assert tracing.trace_level() == "off" and not tracing.tracing_on()


def test_tracing_on_requires_level_AND_config(monkeypatch):
    monkeypatch.setenv("RAG_TRACE_LEVEL", "generations")
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    assert not tracing.tracing_on()          # level set but NOT configured -> off (gate on config, not import)
    _creds(monkeypatch)
    assert tracing.tracing_on()


def test_start_generation_is_none_when_off(monkeypatch):
    monkeypatch.setenv("RAG_TRACE_LEVEL", "off")
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.setattr(tracing, "_get_client", lambda: None)
    gen = tracing.start_generation(model="m", input="x")
    assert gen is None
    tracing.finish_generation(gen, output="y", latency_ms=5.0)  # no raise on a None gen (tracing off)


def test_open_before_close_after_gives_a_real_span(monkeypatch):
    fake = _FakeLF()
    monkeypatch.setenv("RAG_TRACE_LEVEL", "verbose"); _creds(monkeypatch)
    monkeypatch.setattr(tracing, "_get_client", lambda: fake)
    # start_generation opens the observation at the CALL START -- before the result is known.
    gen = tracing.start_generation(model="granite", input="the prompt", label="clause-group", role="general")
    assert len(fake.calls) == 1
    c = fake.calls[0]
    assert c["start"]["model"] == "granite" and c["start"]["as_type"] == "generation"
    assert c["start"]["input"] == "the prompt"                       # verbose captures the prompt
    assert c["start"]["metadata"]["label"] == "clause-group"
    assert "latency_ms" not in c["start"]["metadata"]               # unknown at start -> carried on finish
    assert "update" not in c and "ended" not in c                   # not ended yet (span is open, timing real)
    tracing.finish_generation(gen, output="the answer", usage={"input": 100, "output": 20}, latency_ms=42.0)
    assert c["update"]["output"] == "the answer"
    assert c["update"]["usage_details"] == {"input": 100, "output": 20}
    assert c["update"]["metadata"]["latency_ms"] == 42.0 and c["ended"] is True


def test_real_cost_is_emitted_as_cost_details(monkeypatch):
    fake = _FakeLF()
    monkeypatch.setenv("RAG_TRACE_LEVEL", "generations"); _creds(monkeypatch)
    monkeypatch.setattr(tracing, "_get_client", lambda: fake)
    gen = tracing.start_generation(model="m", label="party")
    tracing.finish_generation(gen, usage={"input": 10, "output": 2}, cost=3.52e-06)
    assert fake.calls[0]["update"]["cost_details"] == {"total": 3.52e-06}  # OpenRouter pass-through cost


def test_no_cost_omits_cost_details(monkeypatch):
    fake = _FakeLF()
    monkeypatch.setenv("RAG_TRACE_LEVEL", "generations"); _creds(monkeypatch)
    monkeypatch.setattr(tracing, "_get_client", lambda: fake)
    gen = tracing.start_generation(model="m")
    tracing.finish_generation(gen, usage={"input": 10, "output": 2}, cost=None)
    assert fake.calls[0]["update"]["cost_details"] is None  # no cost -> Langfuse prices from its own table


def test_time_to_first_token_is_emitted_as_completion_start(monkeypatch):
    import datetime as dt
    fake = _FakeLF()
    monkeypatch.setenv("RAG_TRACE_LEVEL", "generations"); _creds(monkeypatch)
    monkeypatch.setattr(tracing, "_get_client", lambda: fake)
    ttft = dt.datetime(2026, 9, 20, tzinfo=dt.timezone.utc)
    gen = tracing.start_generation(model="m", stage="astream_text")
    tracing.finish_generation(gen, latency_ms=50.0, completion_start_time=ttft)  # 0048: queue/decode split
    assert fake.calls[0]["update"]["completion_start_time"] == ttft


def test_generations_level_omits_prompt_and_output(monkeypatch):
    fake = _FakeLF()
    monkeypatch.setenv("RAG_TRACE_LEVEL", "generations"); _creds(monkeypatch)
    monkeypatch.setattr(tracing, "_get_client", lambda: fake)
    gen = tracing.start_generation(model="m", input="secret prompt")
    tracing.finish_generation(gen, output="secret out", usage={"input": 5, "output": 2})
    c = fake.calls[0]
    assert c["start"]["input"] is None and c["update"]["output"] is None   # numbers only, not the text
    assert c["update"]["usage_details"] == {"input": 5, "output": 2}       # usage still emitted


def test_record_generation_convenience_still_emits(monkeypatch):
    # the post-hoc convenience (open + immediately end) still works for a caller with only after-call data.
    fake = _FakeLF()
    monkeypatch.setenv("RAG_TRACE_LEVEL", "verbose"); _creds(monkeypatch)
    monkeypatch.setattr(tracing, "_get_client", lambda: fake)
    tracing.record_generation(model="granite", input="the prompt", output="the answer",
                              usage={"input": 100, "output": 20}, latency_ms=42.0, label="clause-group")
    c = fake.calls[0]
    assert c["start"]["metadata"]["label"] == "clause-group" and c["update"]["metadata"]["latency_ms"] == 42.0
    assert c["update"]["output"] == "the answer" and c["ended"] is True
