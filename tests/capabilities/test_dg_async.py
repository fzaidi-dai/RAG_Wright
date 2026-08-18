"""ASYNC-A4 (ADR-0057): docling-graph on our async seam via the injected deadline-bounded client (Approach A,
no fork). Hermetic -- litellm.acompletion is monkeypatched, no network. `_call_api` uses asyncio.run internally,
so it is exercised via asyncio.to_thread (as the real pipeline runs it, off the loop).
"""
from __future__ import annotations

import asyncio
import threading

import litellm
import pytest

import rag_wright.capabilities.dg_extraction as dg
from rag_wright.capabilities.dg_extraction import ExtractionModel, build_pipeline_config
from rag_wright.models import seam

_MODEL = ExtractionModel("granite", "openrouter", "ibm-granite/granite-4.1-8b", "https://openrouter.ai/api/v1")


def _client(tmp_path):
    src = tmp_path / "contract.md"
    src.write_text("Agreement between Acme and Beta.", encoding="utf-8")
    return build_pipeline_config(str(src), _MODEL, max_tokens=1500).llm_client


def test_pipeline_config_injects_our_deadline_bounded_client(tmp_path):
    client = _client(tmp_path)
    # Approach A: docling-graph honors PipelineConfig.llm_client (stages.py:559) -> our client, no fork
    assert client is not None
    assert type(client) is dg._deadline_bounded_client_class()


async def test_call_api_returns_content_and_metadata(tmp_path, monkeypatch):
    client = _client(tmp_path)
    monkeypatch.setattr(client, "_build_request", lambda messages, **k: {"model": "m", "messages": messages})

    async def fake_acompletion(**request):
        return {"choices": [{"message": {"content": '{"parties": []}'}, "finish_reason": "stop"}],
                "model": "m", "usage": {}}

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    content, meta = await asyncio.to_thread(client._call_api, [{"role": "user", "content": "x"}], schema_json="{}")
    assert content == '{"parties": []}' and meta["finish_reason"] == "stop"


async def test_call_api_deadline_cancels_a_stalled_acompletion(tmp_path, monkeypatch):
    monkeypatch.setattr(seam, "_MODEL_DEADLINE_S", 0.05)
    client = _client(tmp_path)
    monkeypatch.setattr(client, "_build_request", lambda messages, **k: {"model": "m", "messages": messages})

    async def stalled_acompletion(**request):  # a slow-drip / wedged peer the socket timeout never catches
        await asyncio.sleep(5.0)

    monkeypatch.setattr(litellm, "acompletion", stalled_acompletion)
    with pytest.raises(seam.ModelCallTimeout):
        await asyncio.to_thread(client._call_api, [{"role": "user", "content": "x"}], schema_json="{}")


async def test_aextract_parties_runs_off_the_event_loop(monkeypatch):
    seen: dict = {}

    def fake_extract(text, model, **kw):
        seen["thread"] = threading.current_thread().name
        seen["text"] = text
        return "RESULT"

    monkeypatch.setattr(dg, "extract_parties", fake_extract)
    out = await dg.aextract_parties("hello", _MODEL, template=object)
    assert out == "RESULT" and seen["text"] == "hello"
    assert seen["thread"] != threading.current_thread().name  # ran off the loop thread (asyncio.to_thread)
