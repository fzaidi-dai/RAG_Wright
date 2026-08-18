"""ASYNC-A3 (ADR-0057): the free-text answer wrappers stream via astream_text. Hermetic -- astream_text is
monkeypatched, no network.
"""
from __future__ import annotations

import rag_wright.capabilities.answer_generator as ag
from rag_wright.capabilities.answer_generator import SeamReasonModel, TaggedFreeTextAnswerModel


async def test_areason_streams_free_text(monkeypatch):
    async def fake_astream(model_id, prompt, **kw):
        return f"reasoned:{prompt}"

    monkeypatch.setattr(ag, "astream_text", fake_astream)
    assert await SeamReasonModel("m").areason("why?") == "reasoned:why?"


async def test_agenerate_streams_then_parses_tags(monkeypatch):
    async def fake_astream(model_id, prompt, **kw):
        # the model answers in the light tags TaggedFreeTextAnswerModel expects
        return "<answer>Acme and Beta are the parties. [c1]</answer>\n<citations>\nc1\n</citations>"

    monkeypatch.setattr(ag, "astream_text", fake_astream)
    result = await TaggedFreeTextAnswerModel("m").agenerate("Who are the parties?")
    assert "Acme and Beta" in result.answer
    assert result.citations == ["c1"]
    assert not result.abstained
