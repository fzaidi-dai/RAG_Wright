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


# --- issue 0025: build_structured records a generation (model + tokens + real cost) on the ainvoke path -----


def test_record_structured_generation_reads_tokens_and_cost_from_raw(monkeypatch):
    from types import SimpleNamespace

    from rag_wright.models import tracing

    raw = SimpleNamespace(
        usage_metadata={"input_tokens": 378, "output_tokens": 155},
        response_metadata={"token_usage": {"cost": 0.0006661, "prompt_tokens": 378, "completion_tokens": 155}})
    result = {"raw": raw, "parsed": SimpleNamespace(verdict="relevant"), "parsing_error": None}
    rec: dict = {}
    monkeypatch.setattr(tracing, "record_generation", lambda **kw: rec.update(kw))
    seam._record_structured_generation(result, "the prompt", "qwen/qwen3.8-27b", "span-relevance", 42.0)
    assert rec["model"] == "qwen/qwen3.8-27b" and rec["label"] == "span-relevance"
    assert rec["stage"] == "build_structured"                      # so every structured caller is attributable
    assert rec["usage"] == {"input": 378, "output": 155}
    assert rec["cost"] == 0.0006661                                # OpenRouter's ACTUAL cost, not a token estimate


def test_record_structured_generation_is_a_noop_on_an_unrecognized_shape(monkeypatch):
    from rag_wright.models import tracing

    called: list = []
    monkeypatch.setattr(tracing, "record_generation", lambda **kw: called.append(kw))
    seam._record_structured_generation("not a dict", "p", "m", None, 1.0)   # not a dict -> skip
    seam._record_structured_generation({"parsed": 1}, "p", "m", None, 1.0)  # no raw -> skip
    assert called == []


def test_raise_on_parse_error_restores_native_include_raw_false_semantics():
    err = ValueError("bad json")
    with pytest.raises(ValueError, match="bad json"):
        seam._raise_on_parse_error({"raw": object(), "parsed": None, "parsing_error": err})
    # no error -> pass the dict through unchanged (so the wrapper can read `raw`)
    assert seam._raise_on_parse_error({"raw": 1, "parsed": "ok", "parsing_error": None})["parsed"] == "ok"
    assert seam._raise_on_parse_error("passthrough") == "passthrough"
