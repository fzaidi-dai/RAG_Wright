"""T60 (FR-C.3, ADR-0026): source training labels for the 3 extended function classes.

CUAD annotates only its 41 `ClauseCategory` types, so it has no spans for the ACORD families the
function taxonomy adds (Indemnification, the indirect/consequential damages waiver, the warranty
disclaimer). We bootstrap them from the SAME CUAD contracts (ACORD stays pure eval -- no leakage): the
operative spans CUAD leaves unlabeled (NONE) are candidates, narrowed by a cheap high-recall KEYWORD
pre-filter and then confirmed by an LLM (DeepSeek via the model-profile seam).

The keyword pre-filter is unit-testable and free; the LLM confirm is the same structured-output seam
pattern the OKF classifier uses (stub-injectable, so the plumbing is tested without a model call).
"""

from __future__ import annotations

from enum import Enum
from typing import Optional, Protocol, runtime_checkable

from pydantic import BaseModel

from rag_wright.packs.contracts.schemas.function import ExtendedFunction
from rag_wright.models.profiles import ModelRole, model_for
from rag_wright.models.seam import build_structured

NONE_LABEL = "NONE"  # matches the T56 classifier's off-taxonomy sentinel

# High-recall keyword pre-filter (lowercased substring match). Precision is the LLM's job; this only
# has to avoid dropping true positives, so it is deliberately broad.
NEW_FUNCTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    ExtendedFunction.INDEMNIFICATION.value: (
        "indemnif", "hold harmless", "harmless from", "defend, indemnify",
    ),
    ExtendedFunction.INDIRECT_DAMAGES_WAIVER.value: (
        "consequential", "indirect damage", "incidental damage", "punitive damage",
        "special damage", "in no event", "lost profits", "loss of profit",
    ),
    ExtendedFunction.WARRANTY_DISCLAIMER.value: (
        "disclaim", "as is", "as-is", "merchantability", "fitness for a particular purpose",
        "implied warrant", "no warrant",
    ),
}


class NewFunctionTag(str, Enum):
    """The LLM confirm's closed output: one of the 3 extended functions, or NONE (none of them)."""

    INDEMNIFICATION = ExtendedFunction.INDEMNIFICATION.value
    INDIRECT_DAMAGES_WAIVER = ExtendedFunction.INDIRECT_DAMAGES_WAIVER.value
    WARRANTY_DISCLAIMER = ExtendedFunction.WARRANTY_DISCLAIMER.value
    NONE = NONE_LABEL


class NewFunctionLabel(BaseModel):
    """Structured LLM output: which extended-function class the span is (or NONE)."""

    label: NewFunctionTag


def keyword_candidates(text: str) -> frozenset[str]:
    """The extended-function labels a span MIGHT be, by keyword (the pre-filter). Empty => not a
    candidate for any new class (skip the LLM call entirely)."""
    low = text.lower()
    hits = {
        label
        for label, keywords in NEW_FUNCTION_KEYWORDS.items()
        if any(kw in low for kw in keywords)
    }
    return frozenset(hits)


def label_prompt(text: str, candidates: frozenset[str]) -> str:
    """The confirm prompt: given the pre-filter's candidate classes, decide the one true class or NONE."""
    options = "\n- ".join(sorted(candidates))
    return (
        "You are labeling a contract clause span for a function classifier. A keyword filter flagged it "
        "as possibly one of these clause types:\n- " + options + "\n\n"
        "Decide which ONE it actually is, or NONE if it is none of them (the keyword was incidental). "
        "Judge by the span's operative meaning, not a passing mention.\n\n"
        "Span:\n" + text[:2000]
    )


@runtime_checkable
class NewFunctionConfirmer(Protocol):
    """Span text + candidate classes -> a confirmed label. The seam a test stubs (no model call)."""

    def __call__(self, text: str, candidates: frozenset[str]) -> NewFunctionTag: ...


class SeamNewFunctionConfirmer:
    """The real confirmer: structured output through the model-profile seam on STRUCTURED_REASONING
    (DeepSeek V4 Pro by default; quality-sensitive labeling). Retries a bare `None` (a transient miss
    that `with_structured_output` returns instead of raising), and raises only when it persists."""

    def __init__(self, model_id: Optional[str] = None, *, retries: int = 3) -> None:
        self._runnable = build_structured(
            model_id or model_for(ModelRole.STRUCTURED_REASONING), NewFunctionLabel
        )
        self._retries = retries

    def __call__(self, text: str, candidates: frozenset[str]) -> NewFunctionTag:
        prompt = label_prompt(text, candidates)
        last_error: Exception | None = None
        for _ in range(self._retries):
            try:
                v = self._runnable.invoke(prompt)
            except Exception as e:  # noqa: BLE001 - transient provider/parse error; retry
                last_error = e
                continue
            if v is not None:
                return v.label
        raise last_error or ValueError("no structured output after retries")
