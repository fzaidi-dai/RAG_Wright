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
