"""ASYNC-A3 (ADR-0057): the async tag-parse structured path (astream + client-side parse, bounded re-ask).
Hermetic -- astream_text is monkeypatched, no network.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

import rag_wright.models.tag_structured as ts
from rag_wright.models.tag_structured import build_tag_structured


class _Party(BaseModel):
    name: str = Field(description="the party name")


async def test_ainvoke_streams_then_parses(monkeypatch):
    async def fake_astream(model_id, prompt, **kw):
        return "<name>Acme Corporation</name>"

    monkeypatch.setattr(ts, "astream_text", fake_astream)
    result = await build_tag_structured("m", _Party).ainvoke("Extract the party.")
    assert isinstance(result, _Party) and result.name == "Acme Corporation"


async def test_label_is_threaded_to_astream_text_for_the_deadline_warning(monkeypatch):
    # ADR-0058: the stage label reaches astream_text, so a tag-parse call's timeout names its stage
    seen = {}

    async def fake_astream(model_id, prompt, **kw):
        seen["label"] = kw.get("label")
        return "<name>Acme</name>"

    monkeypatch.setattr(ts, "astream_text", fake_astream)
    out = await build_tag_structured("m", _Party, label="semantic_chunking.discover").ainvoke("q")
    assert out.name == "Acme" and seen["label"] == "semantic_chunking.discover"


async def test_ainvoke_re_asks_on_validation_error_then_succeeds(monkeypatch):
    calls = {"n": 0}

    async def fake_astream(model_id, prompt, **kw):
        calls["n"] += 1
        return "<wrong>bad</wrong>" if calls["n"] == 1 else "<name>Acme</name>"

    monkeypatch.setattr(ts, "astream_text", fake_astream)
    result = await build_tag_structured("m", _Party, retries=1).ainvoke("Extract the party.")
    assert result.name == "Acme" and calls["n"] == 2  # first parse failed -> bounded re-ask
