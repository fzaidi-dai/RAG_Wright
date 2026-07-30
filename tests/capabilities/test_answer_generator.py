"""Answer generation + vision-to-text (T29, FR-C.9/FR-Q.6).

Hermetic tests prove the hard grounding rules in code (no claim without a citation, abstention, dropping
fabricated citations, surfacing confidence) and the vision seam. Live `-m model` runs the real Gemma
generation and a real image transcription (a synthetic PNG).
"""

from __future__ import annotations

import io

import pytest

from rag_wright.capabilities.answer_generator import (
    EvidenceItem,
    GeneratedAnswer,
    _evidence_block,
    generate_answer,
    register_generation,
)
from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.capabilities.vision_to_text import vision_to_text


class _StubModel:
    def __init__(self, answer: GeneratedAnswer) -> None:
        self._answer = answer
        self.prompt: str | None = None

    def generate(self, prompt: str) -> GeneratedAnswer:
        self.prompt = prompt
        return self._answer


class _RaisingModel:
    def generate(self, prompt: str) -> GeneratedAnswer:
        raise AssertionError("the model must not be called when there is no evidence")


_EV = [EvidenceItem(chunk_id="c1", text="Acme and Beta are the parties.")]


# --- grounding, citation, abstention -------------------------------------------------------------


def test_cited_answer_passes_through():
    model = _StubModel(GeneratedAnswer(answer="Acme and Beta.", citations=["c1"], abstained=False))
    result = generate_answer("Who are the parties?", _EV, model=model)
    assert result.answer == "Acme and Beta." and result.citations == ["c1"] and not result.abstained


def test_empty_evidence_abstains_without_calling_the_model():
    result = generate_answer("Who are the parties?", [], model=_RaisingModel())
    assert result.abstained and result.citations == []  # abstain, no fabrication, no model call


def test_answer_with_no_citation_is_coerced_to_abstention():
    model = _StubModel(GeneratedAnswer(answer="Acme and Beta.", citations=[], abstained=False))
    result = generate_answer("q", _EV, model=model)
    assert result.abstained and result.citations == []  # no claim without a citation (FR-Q.6)


def test_fabricated_citations_are_dropped():
    model = _StubModel(GeneratedAnswer(answer="Acme.", citations=["c1", "c99"], abstained=False))
    result = generate_answer("q", _EV, model=model)
    assert result.citations == ["c1"]  # c99 is not in the evidence -> dropped


def test_model_abstention_is_respected():
    model = _StubModel(GeneratedAnswer(answer="Not stated.", citations=[], abstained=True))
    result = generate_answer("q", _EV, model=model)
    assert result.abstained and result.answer == "Not stated."


def test_graph_fact_confidence_is_surfaced_to_the_generator():
    evidence = [EvidenceItem(chunk_id="c1", text="Acme affiliates Beta.", confidence="AMBIGUOUS")]
    model = _StubModel(GeneratedAnswer(answer="Acme affiliates Beta.", citations=["c1"], abstained=False))
    generate_answer("q", evidence, model=model)
    assert "AMBIGUOUS" in model.prompt  # the confidence tag is put in front of the model
    assert "[confidence: AMBIGUOUS]" in _evidence_block(evidence)


def test_registers_under_fr_c_9():
    registry = CapabilityRegistry()
    register_generation(registry)
    reg = registry.get("generation")
    assert reg.name == "generation"
    assert reg.contract is GeneratedAnswer
    assert reg.kind == "agent_skill"  # CAP-REG-1: a single grounded/cited LLM act


# --- vision-to-text ------------------------------------------------------------------------------


class _StubVision:
    def __init__(self) -> None:
        self.seen: dict | None = None

    def image_to_text(self, image: bytes, *, media_type: str) -> str:
        self.seen = {"len": len(image), "media_type": media_type}
        return "transcribed text"


def test_vision_to_text_calls_the_vision_model():
    vision = _StubVision()
    out = vision_to_text(b"\x89PNGfake", model=vision, media_type="image/png")
    assert out == "transcribed text"
    assert vision.seen == {"len": len(b"\x89PNGfake"), "media_type": "image/png"}


def test_vision_to_text_registers_under_its_own_slug():
    from rag_wright.capabilities.vision_to_text import VisionTranscription, register_vision_to_text

    registry = CapabilityRegistry()
    register_vision_to_text(registry)
    reg = registry.get("vision_to_text")
    assert reg.name == "vision_to_text"  # split from generation (ADR-0014)
    assert reg.contract is VisionTranscription
    assert reg.kind == "function"


# --- live Gemma (opt-in): real generation + real image transcription -----------------------------


@pytest.mark.model
def test_live_generation_grounds_and_cites_or_abstains():
    from rag_wright.capabilities.answer_generator import SeamAnswerModel

    evidence = [
        EvidenceItem(chunk_id="docA:0:h", text="This Agreement is between Acme Corporation and Beta LLC."),
        EvidenceItem(chunk_id="docA:1:h", text="This Agreement is governed by the laws of Delaware."),
    ]
    result = generate_answer("Which state's law governs this agreement?", evidence, model=SeamAnswerModel())

    if not result.abstained:
        assert result.citations  # a grounded claim is cited
        assert set(result.citations) <= {"docA:0:h", "docA:1:h"}  # only real evidence ids
        assert result.answer.strip()


@pytest.mark.model
def test_live_vision_to_text_transcribes_a_synthetic_image():
    from PIL import Image, ImageDraw

    from rag_wright.capabilities.vision_to_text import SeamVisionModel

    image = Image.new("RGB", (320, 90), "white")
    ImageDraw.Draw(image).text((10, 35), "HELLO WORLD", fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")

    text = vision_to_text(buffer.getvalue(), model=SeamVisionModel(), media_type="image/png")
    assert "HELLO" in text.upper() or "WORLD" in text.upper()  # the model read the rendered text
