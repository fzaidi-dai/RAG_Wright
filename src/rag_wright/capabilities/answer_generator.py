"""Answer generation (FR-C.9, FR-Q.6, T29): grounded, cited, confidence-aware, abstention-willing.

Produces the final answer from the query-side evidence (fusion/synthesis). It enforces the spec's hard
rule — **no claim without a citation** (FR-Q.6): every non-abstaining answer must cite `chunk_id`s that
are actually in the evidence, and a question the evidence does not support yields an **abstention**, not
a fabrication. It is **confidence-aware**: graph-derived facts carry their confidence tag into the
evidence the model sees (T26 surfaces it; here it is put in front of the generator). Vision-to-text is a
separate capability/slug (`vision_to_text.py`, ADR-0014), though both run on the Gemma 4 class model.

Grounding and citation are enforced in CODE around the model, not left to the prompt: fabricated
citations (ids not in the evidence) are dropped, and an answer that ends up with no valid citation is
coerced to an abstention. The model choice is the `GENERAL` role (no flag here).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

from pydantic import BaseModel

from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.models.profiles import ModelRole, model_for
from rag_wright.models.seam import build_model, build_structured
from rag_wright.util.concurrent import map_concurrent

_ABSTENTION = "The retrieved context does not support an answer."
_SKILL_PATH = Path(__file__).parents[1] / "skills" / "generation" / "SKILL.md"


def generation_method() -> str:
    """The grounded-answer method (the `generation` SKILL body, YAML frontmatter stripped) used as the
    generator's instruction. Authored knowledge (skills/generation/SKILL.md), not a hardcoded string. The
    citation/abstention GUARANTEES are still enforced in code around the model (see `generate_answer`)."""
    text = _SKILL_PATH.read_text(encoding="utf-8")
    if text.startswith("---"):
        marker = text.find("\n---", 3)
        if marker != -1:
            text = text[marker + 4 :]
    return text.strip()


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
    """The real generator: structured output through the model-profile seam (GENERAL role). `temperature`
    defaults to 0; the best-of-N strategy constructs one at temperature>0 to sample diverse completions."""

    def __init__(
        self, model_id: str | None = None, *, temperature: float = 0.0, max_tokens: int | None = None
    ) -> None:
        self._model_id = model_id or model_for(ModelRole.GENERAL)
        self._temperature = temperature
        self._max_tokens = max_tokens

    def generate(self, prompt: str) -> GeneratedAnswer:
        return build_structured(
            self._model_id, GeneratedAnswer, temperature=self._temperature, max_tokens=self._max_tokens
        ).invoke(prompt)


@runtime_checkable
class ReasonModel(Protocol):
    """The free-text reasoning seam (B): analyze the evidence in prose, no forced schema."""

    def reason(self, prompt: str) -> str: ...


class SeamReasonModel:
    """The real reasoner: a plain free-text call through the seam (GENERAL role). Free-text avoids the
    forced-structured/thinking-mode conflict that makes the one-shot structured generate flaky."""

    def __init__(self, model_id: str | None = None) -> None:
        self._model_id = model_id or model_for(ModelRole.GENERAL)

    def reason(self, prompt: str) -> str:
        return str(build_model(self._model_id).invoke(prompt).content)


def _evidence_block(evidence: list[EvidenceItem]) -> str:
    lines = []
    for item in evidence:
        tag = f" [confidence: {item.confidence}]" if item.confidence else ""
        lines.append(f"[{item.chunk_id}]{tag} {item.text}")
    return "\n".join(lines)


def _abstain(text: str = _ABSTENTION) -> GeneratedAnswer:
    return GeneratedAnswer(answer=text, citations=[], abstained=True)


def _finalize(raw: GeneratedAnswer, evidence: list[EvidenceItem]) -> GeneratedAnswer:
    """The code-level guarantees applied to a raw model answer (shared by every generation strategy):
    an abstention stays an abstention; a citation not present in the evidence is dropped (no fabrication);
    an answer left with no valid citation is coerced to an abstention (no claim without a citation, FR-Q.6)."""
    if raw.abstained:
        return _abstain(raw.answer or _ABSTENTION)
    valid_ids = {item.chunk_id for item in evidence}
    citations = [chunk_id for chunk_id in raw.citations if chunk_id in valid_ids]  # drop fabricated
    if not citations:
        return _abstain()
    return GeneratedAnswer(answer=raw.answer, citations=citations, abstained=False)


def _answer_prompt(query: str, evidence: list[EvidenceItem]) -> str:
    return f"{generation_method()}\n\nQuestion: {query}\n\nEvidence:\n{_evidence_block(evidence)}"


def generate_answer(
    query: str, evidence: list[EvidenceItem], *, model: AnswerModel
) -> GeneratedAnswer:
    """Generate a grounded, cited answer — or abstain — enforcing no-claim-without-a-citation in code.

    Empty evidence abstains without a model call. Otherwise the model answers over the evidence block
    (with confidence tags surfaced); any citation not present in the evidence is dropped, and an answer
    left with no valid citation is coerced to an abstention. This is the single-call baseline strategy.
    """
    if not evidence:
        return _abstain()
    return _finalize(model.generate(_answer_prompt(query, evidence)), evidence)


_REASON_HEADER = (
    "STEP 1 — ANALYSIS (not the final answer). Work through ONLY the evidence below: does it support an "
    "answer to the question? Name the specific [chunk_id] items that support each part of a would-be answer; "
    "if an item is framed as an inferred exception/carve-out, note the rule together with its exception. If "
    "the evidence genuinely does not support an answer, say so and why. Do NOT write the final answer yet."
)


def generate_answer_reasoned(
    query: str, evidence: list[EvidenceItem], *, reason_model: ReasonModel, emit_model: AnswerModel
) -> GeneratedAnswer:
    """Strategy B: split generation into a FREE-TEXT reasoning node then a STRUCTURED emit node. The reason
    node analyzes the evidence in prose (where the model is strongest and the forced-structured/thinking-mode
    conflict does not apply); the emit node only FORMATS that conclusion into the `GeneratedAnswer` contract,
    a more constrained call than reason-and-emit in one shot. Same code-level guarantees via `_finalize`;
    empty evidence still abstains without any model call."""
    if not evidence:
        return _abstain()
    block = _evidence_block(evidence)
    analysis = reason_model.reason(
        f"{generation_method()}\n\n{_REASON_HEADER}\n\nQuestion: {query}\n\nEvidence:\n{block}")
    emit_prompt = (
        f"{generation_method()}\n\nQuestion: {query}\n\nEvidence:\n{block}\n\n"
        f"STEP 2 — using your STEP 1 analysis below, emit the final grounded, cited answer now, or abstain "
        f"if the analysis concluded the evidence does not support one.\n\nSTEP 1 analysis:\n{analysis}"
    )
    return _finalize(emit_model.generate(emit_prompt), evidence)


def generate_answer_best_of_n(
    query: str, evidence: list[EvidenceItem], *, model: AnswerModel, n: int = 5, min_answers: int = 1,
    max_concurrency: int = 5,
) -> GeneratedAnswer:
    """Strategy C: sample the single-call generation `n` times (supply a temperature>0 `model` for genuine
    diversity), run CONCURRENTLY, and take the best-cited NON-abstaining sample — abstaining only if fewer
    than `min_answers` samples produced a valid cited answer. Self-consistency against the near-boundary
    abstain flip: one good grounded sample is enough to answer; `min_answers`>1 demands agreement. Empty
    evidence abstains without any model call."""
    if not evidence:
        return _abstain()
    prompt = _answer_prompt(query, evidence)
    raws = map_concurrent([prompt] * n, model.generate, max_concurrency=max_concurrency)
    answered = [f for f in (_finalize(r, evidence) for r in raws if r is not None) if not f.abstained]
    if len(answered) < min_answers:
        return _abstain()
    return max(answered, key=lambda f: len(f.citations))


def register_generation(registry: CapabilityRegistry) -> None:
    """Register answer generation under FR-C.9 (`generation`; vision-to-text is its own slug, ADR-0014)."""
    registry.register(
        "generation",
        contract=GeneratedAnswer,
        kind="agent_skill",  # a single grounded/cited LLM act (CAP-REG-1)
        display_name="Answer generation (grounded, cited, abstains)",
    )
