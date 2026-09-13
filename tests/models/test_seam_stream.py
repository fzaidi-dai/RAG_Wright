"""ASYNC-A3 (ADR-0057): free-text generation via `astream` -- idle-drip detection (`stream_chunk_timeout`) plus
the total wall-clock deadline and bounded retries, accumulating the stream into the full text. Hermetic --
`build_model` is monkeypatched to a fake streaming client, no network.
"""
from __future__ import annotations

import pytest

from rag_wright.models import seam
from tests.async_helpers import FakeStreamingClient


async def test_astream_text_accumulates_chunks(monkeypatch):
    fake = FakeStreamingClient(chunks=("Hel", "lo ", "world"))
    monkeypatch.setattr(seam, "build_model", lambda *a, **k: fake)
    out = await seam.astream_text("m", "prompt")
    assert out == "Hello world" and fake.calls == 1


async def test_astream_text_total_deadline_cancels_a_stalled_stream(monkeypatch):
    monkeypatch.setattr(seam, "_MODEL_DEADLINE_S", 0.05)
    fake = FakeStreamingClient(stall_s=5.0)
    monkeypatch.setattr(seam, "build_model", lambda *a, **k: fake)
    with pytest.raises(seam.ModelCallTimeout):
        await seam.astream_text("m", "prompt")


async def test_astream_text_retries_a_transient_then_succeeds(monkeypatch):
    monkeypatch.setattr(seam, "_backoff_s", lambda _a: 0.0)
    fake = FakeStreamingClient(chunks=("ok",), fail_times=2)
    monkeypatch.setattr(seam, "build_model", lambda *a, **k: fake)
    out = await seam.astream_text("m", "prompt")
    assert out == "ok" and fake.calls == 3


async def test_astream_text_wires_idle_and_no_sdk_retry(monkeypatch):
    # the idle-drip guard is passed to the client, and the SDK's own retry loop is off (our layer is the only one)
    captured: dict = {}

    def fake_build_model(model_id, **kw):
        captured.update(kw)
        return FakeStreamingClient()

    monkeypatch.setattr(seam, "build_model", fake_build_model)
    await seam.astream_text("m", "prompt")
    assert captured.get("stream_chunk_timeout") == seam._STREAM_CHUNK_TIMEOUT_S
    assert captured.get("max_retries") == 0
    # issue 0021: astream_text asks build_model for the cost-capturing client
    assert captured.get("_client_cls") is seam._CostCapturingChatOpenAI


def test_cost_capturing_client_taps_cost_from_the_raw_chunk():
    # issue 0021: LangChain's streaming normalization drops OpenRouter's `cost`; the subclass taps the raw chunk's
    # usage.cost in the (overridable) per-chunk converter. Hermetic -- the base converter is stubbed, no network.
    from langchain_openai import ChatOpenAI

    seen: dict = {}
    monkeypatch_target = "_convert_chunk_to_generation_chunk"
    orig = ChatOpenAI._convert_chunk_to_generation_chunk
    try:
        ChatOpenAI._convert_chunk_to_generation_chunk = lambda self, c, d, b: seen.setdefault("called", True)  # type: ignore[assignment,method-assign]
        client = seam._CostCapturingChatOpenAI(model="m", api_key="k", base_url="http://x")
        client._convert_chunk_to_generation_chunk(
            {"usage": {"cost": 4.92e-05, "prompt_tokens": 18, "completion_tokens": 2}}, object, None)
        assert client._cost_holder.get("cost") == 4.92e-05   # captured
        assert seen.get("called") is True                    # and still delegates to the base converter
    finally:
        ChatOpenAI._convert_chunk_to_generation_chunk = orig  # type: ignore[method-assign]


async def test_astream_text_passes_real_cost_to_record_generation(monkeypatch):
    # issue 0021: the captured cost reaches record_generation (not None -> no $0.00/UNPRICED).
    from rag_wright.models import tracing

    fake = FakeStreamingClient(chunks=("ok",))
    fake._cost_holder = {"cost": 3.5e-06}  # the subclass would populate this from the raw chunk
    monkeypatch.setattr(seam, "build_model", lambda *a, **k: fake)
    monkeypatch.setattr(tracing, "tracing_on", lambda: True)
    rec: dict = {}
    monkeypatch.setattr(tracing, "record_generation", lambda **kw: rec.update(kw))
    out = await seam.astream_text("m", "prompt")
    assert out == "ok"
    assert rec["cost"] == 3.5e-06 and rec["stage"] == "astream_text"


# --- issue 0025 / 0042: build_structured reads tokens + real cost off the raw response ----------------------


def test_usage_from_raw_reads_tokens_and_cost():
    from types import SimpleNamespace

    raw = SimpleNamespace(
        usage_metadata={"input_tokens": 378, "output_tokens": 155},
        response_metadata={"token_usage": {"cost": 0.0006661, "prompt_tokens": 378, "completion_tokens": 155}})
    assert seam._usage_from_raw(raw) == (378, 155, 0.0006661)      # OpenRouter's ACTUAL cost, not a token estimate


def test_usage_from_raw_degrades_on_missing_shapes():
    from types import SimpleNamespace

    assert seam._usage_from_raw(None) == (0, 0, None)              # no raw -> zeros + unknown cost
    assert seam._usage_from_raw(SimpleNamespace()) == (0, 0, None)  # no usage_metadata / token_usage -> same


def test_raise_on_parse_error_restores_native_include_raw_false_semantics():
    err = ValueError("bad json")
    with pytest.raises(ValueError, match="bad json"):
        seam._raise_on_parse_error({"raw": object(), "parsed": None, "parsing_error": err})
    # no error -> pass the dict through unchanged (so the wrapper can read `raw`)
    assert seam._raise_on_parse_error({"raw": 1, "parsed": "ok", "parsing_error": None})["parsed"] == "ok"
    assert seam._raise_on_parse_error("passthrough") == "passthrough"


# --- issue 0042: the seam records usage into an active scope, with tracing OFF (usage is a property of the call) --

class _UsageChunk:
    def __init__(self, content, usage_metadata=None):
        self.content = content
        if usage_metadata is not None:
            self.usage_metadata = usage_metadata


class _UsageStreamingClient:
    """A streaming fake that emits usage_metadata on its final chunk and carries a cost holder (issue 0021)."""

    def __init__(self, *, input_tokens, output_tokens, cost):
        self._u = {"input_tokens": input_tokens, "output_tokens": output_tokens}
        self._cost_holder = {"cost": cost}
        self.calls = 0

    async def astream(self, prompt=None):  # noqa: ANN201
        self.calls += 1
        yield _UsageChunk("answer ")
        yield _UsageChunk("text", usage_metadata=self._u)  # usage on the final chunk (stream_usage=True)


async def test_astream_text_records_tokens_and_cost_into_the_scope_with_tracing_off(monkeypatch):
    from rag_wright.models import tracing
    from rag_wright.models.usage import usage_scope

    monkeypatch.setattr(tracing, "tracing_on", lambda: False)  # req#4: no tracing
    fake = _UsageStreamingClient(input_tokens=120, output_tokens=44, cost=0.0009)
    monkeypatch.setattr(seam, "build_model", lambda *a, **k: fake)
    with usage_scope() as u:
        out = await seam.astream_text("qwen3.8-27b-modal-or", "prompt")
    assert out == "answer text"
    assert u.calls == 1 and u.input_tokens == 120 and u.output_tokens == 44   # round trip + tokens counted
    assert round(u.cost_usd, 4) == 0.0009 and u.by_model["qwen3.8-27b-modal-or"].calls == 1


async def test_astream_text_counts_the_call_even_when_the_model_returns_no_usage(monkeypatch):
    # req#1: the round trip is counted even with no usage_metadata; its cost is unknown, not $0.
    from rag_wright.models import tracing
    from rag_wright.models.usage import usage_scope

    monkeypatch.setattr(tracing, "tracing_on", lambda: False)
    monkeypatch.setattr(seam, "build_model", lambda *a, **k: FakeStreamingClient(chunks=("hi",)))
    with usage_scope() as u:
        await seam.astream_text("m", "p")
    assert u.calls == 1 and u.calls_without_cost == 1 and u.input_tokens == 0


def test_build_structured_records_into_the_scope_with_tracing_off(monkeypatch):
    from types import SimpleNamespace

    from langchain_core.runnables import RunnableLambda

    from rag_wright.models import tracing
    from rag_wright.models.usage import usage_scope

    monkeypatch.setattr(tracing, "tracing_on", lambda: False)  # req#4: capture without tracing
    raw = SimpleNamespace(usage_metadata={"input_tokens": 300, "output_tokens": 25},
                          response_metadata={"token_usage": {"cost": 0.0012}})

    class _FakeModel:
        def with_structured_output(self, schema, **kw):
            assert kw.get("include_raw") is True  # the seam always forces include_raw internally (issue 0042)
            return RunnableLambda(lambda x: {"raw": raw, "parsed": SimpleNamespace(v="ok"), "parsing_error": None})

    monkeypatch.setattr(seam, "build_model", lambda *a, **k: _FakeModel())
    with usage_scope() as u:
        parsed = seam.build_structured("qwen3.8-27b-modal-or", object).invoke("prompt")
    assert parsed.v == "ok"                                    # caller's exact shape (parsed), include_raw stripped
    assert u.calls == 1 and u.input_tokens == 300 and u.output_tokens == 25
    assert round(u.cost_usd, 4) == 0.0012 and u.by_model["qwen3.8-27b-modal-or"].latency_ms_total >= 0.0
