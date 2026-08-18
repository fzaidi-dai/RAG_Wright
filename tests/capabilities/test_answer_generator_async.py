"""ASYNC-A3 (ADR-0057): the free-text answer wrappers stream via astream_text. Hermetic -- astream_text is
monkeypatched, no network.
"""
from __future__ import annotations

import rag_wright.capabilities.answer_generator as ag
from rag_wright.capabilities.answer_generator import (
    EvidenceItem,
    GeneratedAnswer,
    SeamReasonModel,
    TaggedFreeTextAnswerModel,
    agenerate_answer,
)


class _AsyncModel:
    """A stub AnswerModel whose agenerate returns a fixed raw answer (records the prompt it saw)."""

    def __init__(self, raw: GeneratedAnswer) -> None:
        self._raw = raw
        self.calls = 0

    async def agenerate(self, prompt: str) -> GeneratedAnswer:
        self.calls += 1
        return self._raw


async def test_agenerate_answer_finalizes_a_cited_answer():
    ev = [EvidenceItem(chunk_id="c1", text="Acme indemnifies Beta.")]
    model = _AsyncModel(GeneratedAnswer(answer="Acme indemnifies Beta. [c1]", citations=["c1"]))
    out = await agenerate_answer("Who indemnifies whom?", ev, model=model)
    assert out.citations == ["c1"] and not out.abstained


async def test_agenerate_answer_empty_evidence_abstains_without_a_model_call():
    model = _AsyncModel(GeneratedAnswer(answer="should not be used", citations=["x"]))
    out = await agenerate_answer("q", [], model=model)
    assert out.abstained is True and model.calls == 0  # no model call on empty evidence (FR-Q.6)


async def test_agenerate_answer_drops_fabricated_citations_then_abstains():
    ev = [EvidenceItem(chunk_id="c1", text="real evidence")]
    model = _AsyncModel(GeneratedAnswer(answer="fabricated [c9]", citations=["c9"]))  # c9 not in evidence
    out = await agenerate_answer("q", ev, model=model)
    assert out.abstained is True  # no valid citation left -> abstain (no claim without a citation)


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
