"""Answer generation (FR-C.9, FR-Q.6, T29): grounded, cited, confidence-aware, abstention-willing.

Produces the final answer from the query-side evidence (fusion/synthesis). It enforces the spec's hard
rule — **no claim without a citation** (FR-Q.6): every non-abstaining answer must cite `chunk_id`s that
are actually in the evidence, and a question the evidence does not support yields an **abstention**, not
a fabrication. It is **confidence-aware**: graph-derived facts carry their confidence tag into the
evidence the model sees (T26 surfaces it; here it is put in front of the generator). One capability with
vision-to-text (`vision_to_text.py`), both on the Gemma 4 class model via the model-profile seam.

Grounding and citation are enforced in CODE around the model, not left to the prompt: fabricated
citations (ids not in the evidence) are dropped, and an answer that ends up with no valid citation is
coerced to an abstention. The model choice is the `GENERAL` role (no flag here).
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from pydantic import BaseModel

from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.models.profiles import ModelRole, model_for
from rag_wright.models.seam import build_structured

_ABSTENTION = "The retrieved context does not support an answer."
_ANSWER_PROMPT = (
    "Answer the question using ONLY the evidence below. Cite the bracketed chunk id that supports each "
    "claim in `citations`. If the evidence does not support an answer, set abstained=true and do not "
    "guess. When you rely on a graph-derived fact, respect its confidence tag."
)


class EvidenceItem(BaseModel):
    """One piece of grounding evidence: a chunk's text, its `chunk_id` (the citation), and — for a
    graph-derived fact — its confidence tag (surfaced to the generator, FR-S.4)."""

    chunk_id: str
    text: str
    confidence: Optional[str] = None  # graph-fact confidence tag; None for plain retrieved text


class GeneratedAnswer(BaseModel):
    """The generated answer (FR-C.9): grounded text, the cited chunk_ids, and whether it abstained."""

    answer: str
    citations: list[str]  # chunk_ids actually in the evidence (no claim without a citation, FR-Q.6)
    abstained: bool


@runtime_checkable
class AnswerModel(Protocol):
    """The generation seam: produce a `GeneratedAnswer` for a grounded prompt (structured output)."""

    def generate(self, prompt: str) -> GeneratedAnswer: ...


class SeamAnswerModel:
    """The real generator: structured output through the model-profile seam (Gemma 4 GENERAL role)."""

    def __init__(self, model_id: str | None = None) -> None:
        self._model_id = model_id or model_for(ModelRole.GENERAL)

    def generate(self, prompt: str) -> GeneratedAnswer:
        return build_structured(self._model_id, GeneratedAnswer).invoke(prompt)


def _evidence_block(evidence: list[EvidenceItem]) -> str:
    lines = []
    for item in evidence:
        tag = f" [confidence: {item.confidence}]" if item.confidence else ""
        lines.append(f"[{item.chunk_id}]{tag} {item.text}")
    return "\n".join(lines)


def _abstain(text: str = _ABSTENTION) -> GeneratedAnswer:
    return GeneratedAnswer(answer=text, citations=[], abstained=True)


def generate_answer(
    query: str, evidence: list[EvidenceItem], *, model: AnswerModel
) -> GeneratedAnswer:
    """Generate a grounded, cited answer — or abstain — enforcing no-claim-without-a-citation in code.

    Empty evidence abstains without a model call. Otherwise the model answers over the evidence block
    (with confidence tags surfaced); any citation not present in the evidence is dropped, and an answer
    left with no valid citation is coerced to an abstention.
    """
    if not evidence:
        return _abstain()

    prompt = f"{_ANSWER_PROMPT}\n\nQuestion: {query}\n\nEvidence:\n{_evidence_block(evidence)}"
    raw = model.generate(prompt)
    if raw.abstained:
        return _abstain(raw.answer or _ABSTENTION)

    valid_ids = {item.chunk_id for item in evidence}
    citations = [chunk_id for chunk_id in raw.citations if chunk_id in valid_ids]  # drop fabricated
    if not citations:  # no claim without a citation (FR-Q.6): coerce to abstention
        return _abstain()
    return GeneratedAnswer(answer=raw.answer, citations=citations, abstained=False)


def register_generation(registry: CapabilityRegistry) -> None:
    """Register generation under FR-C.9 (`generation`, one capability incl. vision-to-text)."""
    registry.register(
        "generation",
        contract=GeneratedAnswer,
        kind="function",
        display_name="Answer generation (grounded, cited, abstains) + vision-to-text",
    )
