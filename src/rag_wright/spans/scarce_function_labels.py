"""Step-4 (classifier rare-class improvement): source SILVER training labels for the SCARCE CUAD function
classes (0.00-recall / <35 real train spans). Same T60 pattern as `new_function_labels.py`: the operative
spans CUAD leaves unlabeled (NONE) are candidates -- these classes are under-annotated, so their real
instances hide in the NONE pool -- narrowed by a cheap high-recall KEYWORD pre-filter, then confirmed by an
LLM. Unlike T60 these are EXISTING `ClauseCategory` types (they have CUAD gold too); the silver only augments
TRAINING (mined from train contracts; the SEED=0 holdout stays pure gold, no leakage).

Confirmer defaults to the GENERAL model (Gemma) per the benchmarked model decisions -- its structured
thinking-disable (ADR-0032) makes this single-label forced call reliable; A/B against DeepSeek if it
underperforms. Keyword pre-filter is unit-testable + free; the confirmer is stub-injectable.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from pydantic import BaseModel

from rag_wright.contracts.function import canonical_function
from rag_wright.models.profiles import ModelRole, model_for
from rag_wright.models.seam import build_structured

NONE_LABEL = "NONE"

# High-recall keyword pre-filter (lowercased substring). Broad on purpose -- precision is the LLM's job; this
# only must avoid dropping true positives. Keys MUST be canonical FUNCTION_LABELS.
SCARCE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Unlimited/All-You-Can-Eat-License": (
        "unlimited", "all-you-can-eat", "all you can eat", "without limit", "no limit",
        "unrestricted", "no restriction on the number",
    ),
    "Irrevocable Or Perpetual License": (
        "irrevocable", "perpetual", "in perpetuity",
    ),
    "Notice Period To Terminate Renewal": (
        "renew", "auto-renew", "automatically renew", "written notice", "days notice",
        "days' notice", "prior written notice", "non-renew", "not to renew",
    ),
    "Most Favored Nation": (
        "most favored", "most-favored", "no less favorable", "as favorable as", "mfn",
    ),
    "Non-Disparagement": (
        "disparage", "denigrate", "defame",
    ),
    "Third Party Beneficiary": (
        "third party beneficiar", "third-party beneficiar", "no third party",
    ),
}

_VALID = {canonical_function(k) for k in SCARCE_KEYWORDS}  # canonical-guard the keys at import
assert None not in _VALID, "SCARCE_KEYWORDS keys must be canonical FUNCTION_LABELS"


class ScarceLabel(BaseModel):
    """Structured confirm output: the chosen scarce label string (validated to the candidates by the caller)."""

    label: str


def scarce_candidates(text: str) -> frozenset[str]:
    """The scarce labels a span MIGHT be, by keyword. Empty => skip the LLM entirely."""
    low = text.lower()
    return frozenset(
        label for label, kws in SCARCE_KEYWORDS.items() if any(kw in low for kw in kws)
    )


def scarce_prompt(text: str, candidates: frozenset[str]) -> str:
    options = "\n- ".join(sorted(candidates))
    return (
        "You label a contract clause span for a function classifier. A keyword filter flagged it as possibly "
        "one of these clause types:\n- " + options + "\n\n"
        "Decide which ONE it ACTUALLY is by its operative meaning (not a passing mention), or answer NONE if "
        "it is none of them. Respond with EXACTLY one of the type strings above, or NONE.\n\n"
        "Span:\n" + text[:2000]
    )


@runtime_checkable
class ScarceConfirmer(Protocol):
    def __call__(self, text: str, candidates: frozenset[str]) -> str: ...


class SeamScarceConfirmer:
    """The real confirmer: structured output through the model-profile seam. Defaults to GENERAL (Gemma);
    pass `model_id` to A/B another model (e.g. DeepSeek). Returns a validated label in `candidates` or NONE
    (a hallucinated / off-list label is treated as NONE, conservative). Retries a transient bare `None`."""

    def __init__(self, model_id: Optional[str] = None, *, retries: int = 3) -> None:
        self._runnable = build_structured(model_id or model_for(ModelRole.GENERAL), ScarceLabel)
        self._retries = retries

    def __call__(self, text: str, candidates: frozenset[str]) -> str:
        prompt = scarce_prompt(text, candidates)
        last_error: Exception | None = None
        for _ in range(self._retries):
            try:
                v = self._runnable.invoke(prompt)
            except Exception as e:  # noqa: BLE001 - transient provider/parse error; retry
                last_error = e
                continue
            if v is not None:
                canon = canonical_function(v.label)
                return canon if canon in candidates else NONE_LABEL
        raise last_error or ValueError("no structured output after retries")
