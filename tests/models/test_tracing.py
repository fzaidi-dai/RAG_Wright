"""Engine issue 0017: LLM tracing gate + emission. Hermetic -- a fake langfuse client, no network."""

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


def test_record_generation_is_a_noop_when_off(monkeypatch):
    monkeypatch.setenv("RAG_TRACE_LEVEL", "off")
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    fake = _FakeLF()
    monkeypatch.setattr(tracing, "_get_client", lambda: None)  # what _get_client returns when tracing is off
    tracing.record_generation(model="m", input="x", output="y")  # no raise, nothing emitted
    assert fake.calls == []


def test_record_generation_emits_a_generation_when_verbose(monkeypatch):
    fake = _FakeLF()
    monkeypatch.setenv("RAG_TRACE_LEVEL", "verbose"); _creds(monkeypatch)
    monkeypatch.setattr(tracing, "_get_client", lambda: fake)
    tracing.record_generation(model="granite", input="the prompt", output="the answer",
                              usage={"input": 100, "output": 20}, latency_ms=42.0,
                              label="clause-group", role="general")
    assert len(fake.calls) == 1
    c = fake.calls[0]
    assert c["start"]["model"] == "granite" and c["start"]["as_type"] == "generation"
    assert c["start"]["input"] == "the prompt"                       # verbose captures the prompt
    assert c["start"]["metadata"]["label"] == "clause-group" and c["start"]["metadata"]["latency_ms"] == 42.0
    assert c["update"]["usage_details"] == {"input": 100, "output": 20}
    assert c["update"]["output"] == "the answer" and c["ended"] is True


def test_real_cost_is_emitted_as_cost_details(monkeypatch):
    fake = _FakeLF()
    monkeypatch.setenv("RAG_TRACE_LEVEL", "generations"); _creds(monkeypatch)
    monkeypatch.setattr(tracing, "_get_client", lambda: fake)
    tracing.record_generation(model="m", usage={"input": 10, "output": 2}, cost=3.52e-06, label="party")
    assert fake.calls[0]["update"]["cost_details"] == {"total": 3.52e-06}  # OpenRouter pass-through cost


def test_no_cost_omits_cost_details(monkeypatch):
    fake = _FakeLF()
    monkeypatch.setenv("RAG_TRACE_LEVEL", "generations"); _creds(monkeypatch)
    monkeypatch.setattr(tracing, "_get_client", lambda: fake)
    tracing.record_generation(model="m", usage={"input": 10, "output": 2}, cost=None)
    assert fake.calls[0]["update"]["cost_details"] is None  # no cost -> Langfuse prices from its own table


def test_generations_level_omits_prompt_and_output(monkeypatch):
    fake = _FakeLF()
    monkeypatch.setenv("RAG_TRACE_LEVEL", "generations"); _creds(monkeypatch)
    monkeypatch.setattr(tracing, "_get_client", lambda: fake)
    tracing.record_generation(model="m", input="secret prompt", output="secret out", usage={"input": 5, "output": 2})
    c = fake.calls[0]
    assert c["start"]["input"] is None and c["update"]["output"] is None   # numbers only, not the text
    assert c["update"]["usage_details"] == {"input": 5, "output": 2}       # usage still emitted
