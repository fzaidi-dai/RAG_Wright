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
    """The ingestion seam (option B): classify a chunk's spans with the chunk as SHARED context, returning one
    ranked `FunctionScore` list per span (aligned to the input order). One LLM call per chunk, not per span."""

    def classify_spans(self, chunk_text: str, span_texts: list[str]) -> list[list[FunctionScore]]: ...


class RawScore(BaseModel):
    """The LLM's raw (pre-validation) score: a free-text function label + coarse confidence, filtered downstream."""

    function: str
    confidence: str


class ClauseFunctionClassification(BaseModel):
    """One span/clause's structured output: applicable functions RANKED primary-first, each with a coarse
    `high|medium|low` confidence. Kept raw (str fields) so post-processing can drop LLM slop rather than fail the
    whole call."""

    functions: list[RawScore]


class BatchSpanClassification(BaseModel):
    """The batched (option B) output: one `ClauseFunctionClassification` per input span, aligned to input order."""

    spans: list[ClauseFunctionClassification]


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


_BATCH_PROMPT = (
    "You are classifying the operative provisions of ONE contract section by their legal function. Read the WHOLE "
    "section for context, then classify EACH numbered span below by the function(s) it serves -- ranked "
    "most-relevant first, each with a coarse confidence (high, medium, or low). A span usually serves one "
    "function; some serve more. Use ONLY these function types; if a span serves none, return an empty list for "
    "it. Return exactly one result per span, in the same order.\n\nFunction types:\n{labels}\n\n"
    "SECTION (context):\n{context}\n\nSPANS:\n{spans}"
)


class LlmBatchClauseClassifier:
    """Option B: classify all of a chunk's spans in ONE LLM call, the chunk as shared context. `runnable` is the
    injected structured seam (`.invoke(prompt) -> BatchSpanClassification`); production wires the graph-building
    model. Robust: a failure or a mis-aligned response degrades to per-span empty (spans become NONE, never a
    crash), and the output is force-aligned to the input span count."""

    def __init__(self, runnable: Any) -> None:
        self._runnable = runnable

    def classify_spans(self, chunk_text: str, span_texts: list[str]) -> list[list[FunctionScore]]:
        if not span_texts:
            return []
        from rag_wright.contracts.function import FUNCTION_LABELS

        numbered = "\n".join(f"[{i}] {t}" for i, t in enumerate(span_texts))
        prompt = _BATCH_PROMPT.format(labels="\n".join(FUNCTION_LABELS), context=chunk_text, spans=numbered)
        try:
            raw = self._runnable.invoke(prompt)
        except Exception:  # noqa: BLE001 - a classify failure degrades to NO function per span (never crash)
            return [[] for _ in span_texts]
        if not isinstance(raw, BatchSpanClassification):
            return [[] for _ in span_texts]
        per_span = raw.spans
        return [_to_scores(per_span[i]) if i < len(per_span) else [] for i in range(len(span_texts))]


class LegalBertClauseAdapter:
    """Back-compat: wrap the span-level single-label LegalBERT classifier. Its one label becomes the PRIMARY at
    `high` confidence; NONE / off-taxonomy -> [] (no function). It is span-level, so `classify_spans` ignores the
    chunk context (each span classified independently, exactly as today)."""

    def __init__(self, classifier: Any) -> None:
        self._classifier = classifier

    def classify(self, clause_text: str) -> list[FunctionScore]:
        return self.classify_spans("", [clause_text])[0]

    def classify_spans(self, chunk_text: str, span_texts: list[str]) -> list[list[FunctionScore]]:  # noqa: ARG002
        out: list[list[FunctionScore]] = []
        for label in self._classifier.classify(span_texts):
            canon = canonical_function(label)
            out.append([FunctionScore(function=canon, confidence=FunctionConfidence.HIGH)] if canon else [])
        return out


def production_llm_clause_classifier(model_id: str) -> LlmClauseClassifier:
    """Wire the single-clause LLM classifier over the model-profile structured seam (ingestion uses server-side
    `build_structured`, ADR-0045). Lazy import so this module stays import-light and hermetic."""
    from rag_wright.models.seam import build_structured

    return LlmClauseClassifier(build_structured(model_id, ClauseFunctionClassification))


def production_batch_clause_classifier(model_id: str) -> LlmBatchClauseClassifier:
    """Wire the BATCHED (option B) clause classifier over the structured seam -- the ingestion default."""
    from rag_wright.models.seam import build_structured

    return LlmBatchClauseClassifier(build_structured(model_id, BatchSpanClassification))
