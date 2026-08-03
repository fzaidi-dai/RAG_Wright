"""JUDGE-SEMANTIC (ADR-0040): the narrowed LLM semantic judge -- Layer 3 of the neuro-symbolic
extraction-fidelity cascade, and the ONLY place an LLM is spent on judging.

Layers 1-2 (lexical grounding + symbolic SHACL) clear the type / cardinality / deontic / textual-anchor
errors deterministically. What remains is the irreducible core: the closed SEMANTIC dimensions
(`SEMANTIC_DIMENSIONS`) whose value is a READING of the clause with no surface form to check --
`mutuality=mutual` vs `unilateral`, `favorability`, `party_asymmetry`, `cap_basis`, the consent regimes,
etc. A wrong reading (mutual asserted on a one-sided clause) is invisible to Layers 1-2 and needs a model.

For each surviving (non-AMBIGUOUS) assertion on a semantic dimension, a targeted verify-or-refute call asks:
does a faithful reading of THIS clause support this property? A refuted assertion is downgraded to AMBIGUOUS,
exactly like `reground` / `symbolic_validate` -- kept but flagged. Because Layers 1-2 already cleared the
deterministic errors, this call class is small and focused.

Model-neutral: the structured call goes through the injected `structured_factory` (the model-profile seam by
default), so the PRODUCT runs it on self-hosted Granite (vLLM/A100, ADR-0039), never a hardcoded provider.
The factory is injectable so the gate is hermetically testable with a fake judge -- no LLM, no network. A
judge that fails/returns None leaves the assertion untouched (conservative: never downgrade on a judge error).
Ingestion-side only; NOT on queries (KG-5d: judging the query text false-flags real constraints).
"""

from __future__ import annotations

from typing import Callable, Optional

from pydantic import BaseModel

from rag_wright.contracts.property import CLOSED_VOCAB, ClausePropertyRecord, PropertyDimension
from rag_wright.contracts.provenance import ConfidenceTag
from rag_wright.models.seam import build_structured
from rag_wright.spans.property_grounding import GROUNDING_CUES
from rag_wright.util.concurrent import map_concurrent

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

_PROMPT = (
    "You audit a legal-clause property extraction for faithfulness to the TEXT. A property was extracted "
    "from the clause below. Decide whether a careful reading of THIS clause SUPPORTS that property. Answer "
    "supported=true only if the clause genuinely supports it; supported=false if the clause does not support "
    "it or contradicts it. Be strict: mere plausibility is not support -- absence of support in this clause "
    "means supported=false.\n\n"
    "Property: {dimension} = {value}\nMeaning: {gloss}\n\nClause:\n{clause}"
)


class SemanticVerdict(BaseModel):
    """The judge's ruling on one semantic assertion: is the reading supported by the clause text?"""

    supported: bool
    reason: str = ""


# judge_fn: (dimension, value, clause_text) -> verdict, or None if the judge could not rule (left untouched).
JudgeFn = Callable[[PropertyDimension, str, str], Optional[SemanticVerdict]]


def build_semantic_judge_fn(model_id: str, *, structured_factory=build_structured) -> JudgeFn:
    """Wire a Granite-backed verify-or-refute `JudgeFn` through the model seam (model-neutral; the product
    points the seam at self-hosted vLLM-Granite). `structured_factory` is injected for hermetic testing."""

    def judge(dimension: PropertyDimension, value: str, text: str) -> Optional[SemanticVerdict]:
        prompt = _PROMPT.format(
            dimension=dimension.value,
            value=value,
            gloss=_DIMENSION_GLOSS.get(dimension, dimension.value),
            clause=text,
        )
        return structured_factory(model_id, SemanticVerdict).invoke(prompt)

    return judge


def semantic_judge(
    record: ClausePropertyRecord, text: str, judge_fn: JudgeFn, *, max_concurrency: int = 8
) -> ClausePropertyRecord:
    """Quality gate (ADR-0040 Layer 3): LLM-judge every surviving (non-AMBIGUOUS) assertion on a SEMANTIC
    dimension; downgrade a refuted one to AMBIGUOUS. A no-op when there is nothing semantic to judge. The
    judge calls run concurrently (async + semaphore, per the parallel-LLM rule). A None verdict is a judge
    failure -> the assertion is left untouched (never downgrade on a judge error)."""
    targets = [
        a for a in record.assertions
        if a.dimension in SEMANTIC_DIMENSIONS and a.confidence != ConfidenceTag.AMBIGUOUS
    ]
    if not targets:
        return record
    verdicts = map_concurrent(
        targets, lambda a: judge_fn(a.dimension, a.value, text), max_concurrency=max_concurrency
    )
    refuted = {id(a) for a, v in zip(targets, verdicts) if v is not None and not v.supported}
    if not refuted:
        return record
    new = [
        a.model_copy(update={"confidence": ConfidenceTag.AMBIGUOUS}) if id(a) in refuted else a
        for a in record.assertions
    ]
    return record.model_copy(update={"assertions": new})


def register_extraction_semantic_judge(registry) -> None:
    """Register `extraction_semantic_judge` (function; ADR-0040 Layer 3 LLM semantic gate)."""
    registry.register(
        "extraction_semantic_judge",
        contract=ClausePropertyRecord,
        kind="function",
        display_name="Extraction semantic judge",
    )
