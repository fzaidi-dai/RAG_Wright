"""JUDGE-SEMANTIC (ADR-0040), SKILL-SPLIT: Layer 3 of the neuro-symbolic extraction-fidelity cascade, the ONLY
place an LLM is spent on judging -- split into a SKILL + a deterministic FUNCTION.

Per the capability-architecture principle (a `function` is deterministic and takes no model; a single LLM act is
an authored `agent_skill`; a workflow is a `subgraph`), the semantic judge is two capabilities:

- **`extraction_semantic_judge` (agent_skill)** -- the verify-or-refute reading METHOD, authored as
  `skills/extraction_semantic_judge/SKILL.md` and applied through the model seam (product = Granite, ADR-0039).
  Given one property (`dimension = value`, with its meaning) and the clause text, it returns a raw
  `SemanticVerdict` (supported / reason). `build_semantic_judge_fn` is its runtime; `structured_factory` is
  injected for hermetic tests.
- **`extraction_semantic_gate` (function)** -- `semantic_judge`: DETERMINISTIC, no model. Selects the surviving
  (non-AMBIGUOUS) assertions on a SEMANTIC dimension (`SEMANTIC_DIMENSIONS`), runs the skill over each
  concurrently, and downgrades a refuted one to AMBIGUOUS (kept but flagged), exactly like `reground` /
  `symbolic_validate`. A judge that fails/returns None leaves the assertion untouched (never downgrade on a
  judge error). Ingestion-side only; NOT on queries (KG-5d: judging the query text false-flags real constraints).

Layers 1-2 (lexical grounding + symbolic SHACL) already cleared the type / cardinality / deontic / textual-anchor
errors deterministically, so this LLM call class is small and focused: the closed SEMANTIC dimensions whose value
is a READING with no surface form (`mutuality=mutual` vs `unilateral`, `favorability`, `cap_basis`, ...).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import BaseModel

from rag_wright.contracts.property import CLOSED_VOCAB, ClausePropertyRecord, PropertyDimension
from rag_wright.contracts.provenance import ConfidenceTag
from rag_wright.models.seam import build_structured
from rag_wright.spans.property_grounding import GROUNDING_CUES
from rag_wright.util.concurrent import map_concurrent

_SKILL_PATH = Path(__file__).parents[1] / "skills" / "extraction_semantic_judge" / "SKILL.md"

# The closed SEMANTIC dimensions: a closed vocabulary (so not open-valued) with NO lexical cue (so not
# checkable by the grounding judge) -- their value is a reading, not a surface token. This is exactly the
# residual Layers 1-2 cannot reach.
SEMANTIC_DIMENSIONS: frozenset[PropertyDimension] = frozenset(
    d for d in CLOSED_VOCAB if d not in GROUNDING_CUES
)

# A short plain-language gloss so the judge understands what each (dimension, value) claims about the clause.
_DIMENSION_GLOSS: dict[PropertyDimension, str] = {
    PropertyDimension.MUTUALITY: "whether the obligation runs BOTH ways (mutual) or only one party's (unilateral)",
    PropertyDimension.FAVORABILITY: "which side the term favors (buyer_favorable / seller_favorable)",
    PropertyDimension.PARTY_ASYMMETRY: "whether the terms are the same for both parties (symmetric) or differ per party",
    PropertyDimension.CAP_BASIS: "the shape of the liability cap (a fixed_fee amount vs a multiple_of_fees)",
    PropertyDimension.LAW_MULTIPLICITY: "whether ONE governing law applies (single) or more than one (multiple)",
    PropertyDimension.IP_OWNERSHIP: "who owns the IP (assigned to one party / joint / retained by the originator)",
    PropertyDimension.NONSOLICIT_TARGET: "who may not be solicited (employees / customers)",
    PropertyDimension.RENEWAL_MECHANISM: "how the term renews (auto-renews / requires_notice to renew)",
    PropertyDimension.COC_CONSENT: "how a change of control is treated (consent_required / notice_only / unrestricted)",
    PropertyDimension.ASSIGNMENT_CONSENT: "how assignment is treated (consent_required / notice_only / free)",
    PropertyDimension.MFN_SCOPE: "what a most-favored-nation term covers (price / terms / price_and_terms)",
    PropertyDimension.TERMINATION_RIGHT: "who may terminate for convenience (either_party / one_party)",
}

# The per-call appendix bound onto the SKILL method (the static method teaches the reading; the specific
# property + clause are appended at call time, the compliance_judgment `_PROMPT_TAIL` pattern).
_PROMPT_TAIL = "\n\nProperty: {dimension} = {value}\nMeaning: {gloss}\n\nClause:\n{clause}"


class SemanticVerdict(BaseModel):
    """The judge's ruling on one semantic assertion: is the reading supported by the clause text?"""

    supported: bool
    reason: str = ""


# judge_fn: (dimension, value, clause_text) -> verdict, or None if the judge could not rule (left untouched).
JudgeFn = Callable[[PropertyDimension, str, str], Optional[SemanticVerdict]]


def judgment_method() -> str:
    """The semantic-judge method (the `extraction_semantic_judge` SKILL body, YAML frontmatter stripped) used as
    the judge's system/method prompt. Authored knowledge (skills/extraction_semantic_judge/SKILL.md)."""
    text = _SKILL_PATH.read_text(encoding="utf-8")
    if text.startswith("---"):
        marker = text.find("\n---", 3)
        if marker != -1:
            text = text[marker + 4 :]
    return text.strip()


def _judge_prompt(method: str, dimension: PropertyDimension, value: str, text: str) -> str:
    return method + _PROMPT_TAIL.format(
        dimension=dimension.value, value=value,
        gloss=_DIMENSION_GLOSS.get(dimension, dimension.value), clause=text)


def build_semantic_judge_fn(model_id: str, *, structured_factory=build_structured) -> JudgeFn:
    """The `extraction_semantic_judge` SKILL's runtime: a Granite-backed verify-or-refute `JudgeFn` through the
    model seam (model-neutral; the product points the seam at self-hosted vLLM-Granite). The SKILL.md method is
    the system prompt; the specific property + clause are appended. `structured_factory` is injected for tests."""
    method = judgment_method()

    def judge(dimension: PropertyDimension, value: str, text: str) -> Optional[SemanticVerdict]:
        return structured_factory(model_id, SemanticVerdict).invoke(_judge_prompt(method, dimension, value, text))

    return judge


def build_asemantic_judge_fn(model_id: str, *, structured_factory=build_structured):
    """ASYNC-B2b (ADR-0057): the async twin of `build_semantic_judge_fn` -- the judge call on the async seam
    (`.ainvoke`, true wall-clock deadline)."""
    method = judgment_method()

    async def ajudge(dimension: PropertyDimension, value: str, text: str) -> Optional[SemanticVerdict]:
        return await structured_factory(model_id, SemanticVerdict).ainvoke(
            _judge_prompt(method, dimension, value, text))

    return ajudge


def semantic_judge(
    record: ClausePropertyRecord, text: str, judge_fn: JudgeFn, *, max_concurrency: int = 8
) -> ClausePropertyRecord:
    """`extraction_semantic_gate` (FUNCTION -- deterministic, no model): the ADR-0040 Layer-3 quality gate. Apply
    the `extraction_semantic_judge` SKILL (`judge_fn`) to every surviving (non-AMBIGUOUS) assertion on a SEMANTIC
    dimension; downgrade a refuted one to AMBIGUOUS. A no-op when there is nothing semantic to judge. The judge
    calls run concurrently (async + semaphore, per the parallel-LLM rule). A None verdict is a judge failure ->
    the assertion is left untouched (never downgrade on a judge error). The model lives in the SKILL, not here."""
    targets = [
        a for a in record.assertions
        if a.dimension in SEMANTIC_DIMENSIONS and a.confidence != ConfidenceTag.AMBIGUOUS
    ]
    if not targets:
        return record
    verdicts = map_concurrent(
        targets, lambda a: judge_fn(a.dimension, a.value, text), max_concurrency=max_concurrency
    )
    return _apply_verdicts(record, targets, verdicts)


def _apply_verdicts(record: ClausePropertyRecord, targets: list, verdicts: list) -> ClausePropertyRecord:
    """Downgrade to AMBIGUOUS every target assertion whose verdict refuted it; a None verdict (judge failure)
    leaves the assertion untouched. Shared by the sync and async gates."""
    refuted = {id(a) for a, v in zip(targets, verdicts) if v is not None and not v.supported}
    if not refuted:
        return record
    new = [
        a.model_copy(update={"confidence": ConfidenceTag.AMBIGUOUS}) if id(a) in refuted else a
        for a in record.assertions
    ]
    return record.model_copy(update={"assertions": new})


def _semantic_targets(record: ClausePropertyRecord) -> list:
    return [a for a in record.assertions
            if a.dimension in SEMANTIC_DIMENSIONS and a.confidence != ConfidenceTag.AMBIGUOUS]


async def asemantic_judge(
    record: ClausePropertyRecord, text: str, ajudge_fn: Any, *, max_concurrency: int = 8
) -> ClausePropertyRecord:
    """ASYNC-B2b (ADR-0057): the async twin of `semantic_judge`. Judges each surviving semantic assertion via the
    async judge, concurrently, bounded by a semaphore (native form of the parallel-LLM rule); each call carries
    the true wall-clock deadline. A None verdict leaves the assertion untouched (never downgrade on a failure)."""
    targets = _semantic_targets(record)
    if not targets:
        return record
    sem = asyncio.Semaphore(max_concurrency)

    async def _one(a: Any) -> Any:
        async with sem:
            return await ajudge_fn(a.dimension, a.value, text)

    verdicts = list(await asyncio.gather(*(_one(a) for a in targets)))
    return _apply_verdicts(record, targets, verdicts)


def register_extraction_semantic_judge(registry) -> None:
    """Register `extraction_semantic_judge` as an AGENT_SKILL (ADR-0040 Layer 3): a single grounded LLM
    verify-or-refute reading, authored as `skills/extraction_semantic_judge/SKILL.md` and applied via the seam.
    Typed output = `SemanticVerdict`."""
    registry.register(
        "extraction_semantic_judge",
        contract=SemanticVerdict,
        kind="agent_skill",
        display_name="Extraction semantic judge (clause property -> supported?; authored skill)",
    )


def register_extraction_semantic_gate(registry) -> None:
    """Register `extraction_semantic_gate` (FUNCTION -- deterministic): apply the `extraction_semantic_judge`
    SKILL over each surviving semantic assertion and downgrade a refuted one to AMBIGUOUS. No model."""
    registry.register(
        "extraction_semantic_gate",
        contract=ClausePropertyRecord,
        kind="function",
        display_name="Extraction semantic gate (semantic-dimension AMBIGUOUS downgrade)",
    )
