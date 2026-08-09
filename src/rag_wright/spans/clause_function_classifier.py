"""INGEST-LLM-CLASSIFIER (ADR-0048): the clause-level function-classification seam.

Replaces the span-level, single-label LegalBERT call at ingestion with a FULL-CLAUSE, MULTI-LABEL, confidence-
scored classifier by the graph-building LLM. Two implementations behind one `classify(clause_text) -> [FunctionScore]`
seam: `LlmClauseClassifier` (the default going forward, via the model-profile structured seam) and
`LegalBertClauseAdapter` (wraps the existing single-label classifier for back-compat). Injected into
`production_document_ingest` (a later step); the injection point query-time already had.

Robustness: the post-processor canonicalizes the LLM's labels, drops off-taxonomy / below-floor labels, and caps
the count -- so LLM slop never crashes ingestion, and a runnable failure degrades to NO function (the clause
becomes NONE, exactly as an off-taxonomy span does today)."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from rag_wright.contracts.function import FunctionConfidence, FunctionScore, canonical_function

_FLOOR: frozenset[FunctionConfidence] = frozenset({FunctionConfidence.HIGH, FunctionConfidence.MEDIUM})
_MAX_FUNCTIONS = 3


@runtime_checkable
class ClauseFunctionClassifier(Protocol):
    """The seam: classify a full clause's text into ranked functions (primary first), or [] for no function."""

    def classify(self, clause_text: str) -> list[FunctionScore]: ...


class RawScore(BaseModel):
    """The LLM's raw (pre-validation) score: a free-text function label + coarse confidence, filtered downstream."""

    function: str
    confidence: str


class ClauseFunctionClassification(BaseModel):
    """The LLM clause classifier's structured output: applicable functions RANKED primary-first, each with a
    coarse `high|medium|low` confidence. Kept raw (str fields) so post-processing can drop LLM slop rather than
    fail the whole call."""

    functions: list[RawScore]


def _to_scores(raw: ClauseFunctionClassification) -> list[FunctionScore]:
    """Canonicalize + confidence-floor (>= medium) + cap (<=3), preserving rank. Drops off-taxonomy / NONE /
    below-floor / unparseable-confidence entries (robust to LLM slop)."""
    out: list[FunctionScore] = []
    for r in raw.functions:
        canon = canonical_function(r.function)
        if canon is None:  # off-taxonomy or NONE
            continue
        try:
            conf = FunctionConfidence(r.confidence.strip().lower())
        except ValueError:
            continue
        if conf in _FLOOR:
            out.append(FunctionScore(function=canon, confidence=conf))
            if len(out) >= _MAX_FUNCTIONS:
                break
    return out


_PROMPT = (
    "You are classifying a single contract CLAUSE by its legal function. Read the whole clause (not a fragment) "
    "and list EVERY function it genuinely serves, ranked most-relevant first. A clause may serve more than one "
    "function; most serve exactly one. For each, give a coarse confidence: high, medium, or low. Use ONLY these "
    "function types; if none apply, return an empty list.\n\nFunction types:\n{labels}\n\nClause:\n{text}"
)


class LlmClauseClassifier:
    """Classify a full clause via the graph-building LLM (structured). `runnable` is the injected structured seam
    (`.invoke(prompt) -> ClauseFunctionClassification`); production wires it through the model-profile seam."""

    def __init__(self, runnable: Any) -> None:
        self._runnable = runnable

    def classify(self, clause_text: str) -> list[FunctionScore]:
        from rag_wright.contracts.function import FUNCTION_LABELS

        prompt = _PROMPT.format(labels="\n".join(FUNCTION_LABELS), text=clause_text)
        try:
            raw = self._runnable.invoke(prompt)
        except Exception:  # noqa: BLE001 - a classify failure degrades to NO function (never crash ingestion)
            return []
        if not isinstance(raw, ClauseFunctionClassification):
            return []
        return _to_scores(raw)


class LegalBertClauseAdapter:
    """Back-compat: wrap the span-level single-label LegalBERT classifier as a clause classifier. Its one label
    becomes the PRIMARY at `high` confidence; NONE / off-taxonomy -> [] (no function)."""

    def __init__(self, classifier: Any) -> None:
        self._classifier = classifier

    def classify(self, clause_text: str) -> list[FunctionScore]:
        label = self._classifier.classify([clause_text])[0]
        canon = canonical_function(label)
        return [FunctionScore(function=canon, confidence=FunctionConfidence.HIGH)] if canon else []


def production_llm_clause_classifier(model_id: str) -> LlmClauseClassifier:
    """Wire the real LLM clause classifier over the model-profile structured seam (ingestion uses server-side
    `build_structured`, ADR-0045). Lazy import so this module stays import-light and hermetic."""
    from rag_wright.models.seam import build_structured

    return LlmClauseClassifier(build_structured(model_id, ClauseFunctionClassification))
