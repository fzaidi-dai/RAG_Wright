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

import asyncio
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from rag_wright.contracts.function import (
    FUNCTION_LABELS,
    FunctionConfidence,
    FunctionScore,
    canonical_function,
)

_FLOOR: frozenset[FunctionConfidence] = frozenset({FunctionConfidence.HIGH, FunctionConfidence.MEDIUM})
_MAX_FUNCTIONS = 3

# ADR-0048 Phase A: the structured `function` field advertises the closed label set (the 52 taxonomy labels +
# "OTHER" for a real clause type we lack a label for). Emitted into the JSON schema so guided decoding HARD-
# constrains the model to a valid label (killing the granite-8B failure mode: inventing free-form names like
# "Exclusive Source of Supply" that then fall to NONE), and strongly guides it under function-calling. Kept as a
# `str` field (json_schema_extra is schema-only, not pydantic-enforced), so a stray value never crashes a whole
# sub-batch and the taxonomy-gap channel (`other_label`) still works. "OTHER" is not a FUNCTION_LABELS entry.
_FUNCTION_ENUM: list[str] = [*FUNCTION_LABELS, "OTHER"]


@runtime_checkable
class ClauseFunctionClassifier(Protocol):
    """The ingestion seam (option B): classify a chunk's spans with the chunk as SHARED context, returning one
    ranked `FunctionScore` list per span (aligned to the input order). One LLM call per chunk, not per span."""

    def classify_spans(self, chunk_text: str, span_texts: list[str]) -> list[list[FunctionScore]]: ...


class RawScore(BaseModel):
    """The LLM's raw (pre-validation) score: a function label (one of the 52 taxonomy labels or "OTHER") + coarse
    confidence. When `function` is "OTHER" (a real clause type not in our taxonomy), `other_label` names it -- the
    taxonomy-gap signal (ADR-0048 option 2). Filtered/categorized downstream. `function` advertises the closed
    label enum in the JSON schema (guided decoding), but stays a `str` so a stray never crashes the sub-batch."""

    function: str = Field(json_schema_extra={"enum": _FUNCTION_ENUM})
    confidence: str
    other_label: str = ""


class ClauseFunctionClassification(BaseModel):
    """One span/clause's structured output: applicable functions RANKED primary-first, each with a coarse
    `high|medium|low` confidence. Kept raw (str fields) so post-processing can drop LLM slop rather than fail the
    whole call."""

    functions: list[RawScore]


class SpanFunctions(BaseModel):
    """One span's classification, keyed by its EXPLICIT `span_index` (the `[n]` in the prompt) -- alignment is by
    index, NOT list position, so a dropped/reordered span can't silently shift every later span's label. The
    classifier may OMIT spans it assigns no function."""

    span_index: int
    functions: list[RawScore]


class BatchSpanClassification(BaseModel):
    """The batched (option B) output: per-span classifications keyed by `span_index` (sparse -- no-function spans
    may be omitted)."""

    spans: list[SpanFunctions]


def _to_scores(raw_scores: list[RawScore]) -> list[FunctionScore]:
    """Canonicalize + confidence-floor (>= medium) + cap (<=3), preserving rank. Drops off-taxonomy / NONE /
    below-floor / unparseable-confidence entries (robust to LLM slop)."""
    out: list[FunctionScore] = []
    for r in raw_scores:
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


def categorize_raw(raw_scores: list[RawScore]) -> tuple[list[FunctionScore], list[str]]:
    """ADR-0048 option 2: split a span's raw scores into (in-taxonomy FunctionScores [canonical+floored],
    out-of-taxonomy labels). An entry whose `function` is off-taxonomy / "OTHER" is captured by its real clause
    type (`other_label`, else the raw `function`) -- distinguishing "no function" from "a function we lack a
    label for" (the taxonomy-gap signal), instead of silently collapsing both to NONE."""
    in_tax = _to_scores(raw_scores)
    others: list[str] = []
    for r in raw_scores:
        if canonical_function(r.function) is None:  # off-taxonomy
            # the LLM may follow the convention (function="OTHER", real type in other_label) OR put the real
            # type directly in `function` (with other_label empty/"None"). Prefer other_label ONLY when function
            # is the literal "OTHER"; otherwise the off-taxonomy `function` string IS the real type.
            if r.function.strip().upper() == "OTHER":
                label = r.other_label.strip()
            else:
                label = r.function.strip()
            if label and label.upper() not in ("NONE", "OTHER"):
                others.append(label)
    return in_tax, others


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
        return _to_scores(raw.functions)


_BATCH_CAP = 10  # max spans per LLM call -- a big multi-provision chunk is split into sub-batches (bounded prompt)

_BATCH_PROMPT = (
    "You are classifying the operative provisions of ONE contract section by their legal function. Read the WHOLE "
    "section for context, then classify each numbered span below by the function(s) it serves -- ranked "
    "most-relevant first, each with a coarse confidence (high, medium, or low). For `function`, use EXACTLY one of "
    "the function types listed below. If a span's real function is NOT in the list, set `function` to \"OTHER\" and "
    "put the actual clause type in `other_label` -- do NOT invent a name in `function`. For EACH span you classify, "
    "return its `span_index` (the [n] number) and its functions; OMIT any span that serves no function. Classify "
    "only indices 0..{max_index}.\n\nFunction types:\n{labels}\n\nSECTION (context):\n{context}\n\nSPANS:\n{spans}"
)

_SUBBATCH_CONCURRENCY = 8  # max concurrent sub-batch LLM calls per chunk (async), bounded against provider limits


def _subs(span_texts: list[str]) -> list[tuple[int, list[str]]]:
    """Split spans into sub-batches of at most `_BATCH_CAP`, each a `(start_index, spans)` pair."""
    return [(start, span_texts[start:start + _BATCH_CAP]) for start in range(0, len(span_texts), _BATCH_CAP)]


def _sub_prompt(chunk_text: str, sub: list[str]) -> str:
    """The batch-classify prompt for one sub-batch (shared by the sync and async paths)."""
    from rag_wright.contracts.function import FUNCTION_LABELS

    numbered = "\n".join(f"[{i}] {t}" for i, t in enumerate(sub))
    return _BATCH_PROMPT.format(
        labels="\n".join(FUNCTION_LABELS), context=chunk_text, spans=numbered, max_index=len(sub) - 1)


def _merge_subbatches(results: list[tuple[int, int, Any]], span_texts: list[str]) -> list[list[RawScore]]:
    """Merge sub-batch results into index-aligned per-span scores, aligning by the returned `span_index` (relative
    to each sub-batch, offset by its start) so a dropped/reordered span can't shift later labels."""
    out: list[list[RawScore]] = [[] for _ in span_texts]
    for start, sublen, raw in results:
        if raw is None:
            continue
        for sf in raw.spans:
            idx = start + sf.span_index
            if start <= idx < start + sublen:
                out[idx] = list(sf.functions)
    return out


class LlmBatchClauseClassifier:
    """Option B: classify a chunk's spans with the chunk as shared context. A big chunk is split into sub-batches
    of `_BATCH_CAP` spans (bounded prompt), each ONE LLM call. Output is aligned by the returned `span_index` (not
    list position), so a dropped/reordered span can't shift later labels. `runnable` is the injected structured
    seam (`.invoke(prompt) -> BatchSpanClassification`). Robust: a failed sub-batch leaves its spans empty."""

    def __init__(self, runnable: Any) -> None:
        self._runnable = runnable

    def _classify_raw(self, chunk_text: str, span_texts: list[str]) -> list[list[RawScore]]:
        """Sub-batched LLM calls, run CONCURRENTLY within the chunk (a big multi-provision chunk's sub-batches are
        independent, so a 29-span chunk costs ~1 call's latency, not 3x). Index-aligned RAW per-span scores (the
        LLM's function+confidence strings, NO canonicalize/floor/cap). A failed sub-batch leaves its spans empty."""
        if not span_texts:
            return []
        from concurrent.futures import ThreadPoolExecutor

        subs = _subs(span_texts)

        def _call(item):
            start, sub = item
            try:
                raw = self._runnable.invoke(_sub_prompt(chunk_text, sub))
            except Exception:  # noqa: BLE001 - a failed sub-batch leaves its spans empty (never crash)
                return (start, len(sub), None)
            return (start, len(sub), raw if isinstance(raw, BatchSpanClassification) else None)

        if len(subs) <= 1:  # single sub-batch -> no thread pool
            results = [_call(subs[0])]
        else:  # concurrent sub-batches (independent calls, merged by span_index)
            with ThreadPoolExecutor(max_workers=len(subs)) as ex:
                results = list(ex.map(_call, subs))
        return _merge_subbatches(results, span_texts)

    async def _aclassify_raw(self, chunk_text: str, span_texts: list[str]) -> list[list[RawScore]]:
        """ASYNC-B1 (ADR-0057): the async twin of `_classify_raw`. Sub-batches run concurrently via
        `asyncio.gather` bounded by a `Semaphore` (native form of the sync thread pool); each `.ainvoke` carries
        the true wall-clock deadline. A failed sub-batch -- including a `ModelCallTimeout` (an Exception, so it is
        caught here) -- leaves its spans empty: the degrade path stays reachable and the node never raises."""
        if not span_texts:
            return []
        subs = _subs(span_texts)
        sem = asyncio.Semaphore(_SUBBATCH_CONCURRENCY)

        async def _acall(item):
            start, sub = item
            async with sem:
                try:
                    raw = await self._runnable.ainvoke(_sub_prompt(chunk_text, sub))
                except Exception:  # noqa: BLE001 - failed sub-batch -> empty spans (never crash the document)
                    return (start, len(sub), None)
            return (start, len(sub), raw if isinstance(raw, BatchSpanClassification) else None)

        results = await asyncio.gather(*(_acall(s) for s in subs))
        return _merge_subbatches(results, span_texts)

    def classify_spans(self, chunk_text: str, span_texts: list[str]) -> list[list[FunctionScore]]:
        return [_to_scores(raws) for raws in self._classify_raw(chunk_text, span_texts)]

    async def aclassify_spans(self, chunk_text: str, span_texts: list[str]) -> list[list[FunctionScore]]:
        return [_to_scores(raws) for raws in await self._aclassify_raw(chunk_text, span_texts)]

    def classify_spans_raw(self, chunk_text: str, span_texts: list[str]) -> list[list[RawScore]]:
        """Debug: the RAW per-span scores (pre-floor), to see what the confidence floor drops."""
        return self._classify_raw(chunk_text, span_texts)


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

    return LlmClauseClassifier(build_structured(
        model_id, ClauseFunctionClassification, label="clause_function_classifier.classify"))


def production_batch_clause_classifier(model_id: str) -> LlmBatchClauseClassifier:
    """Wire the BATCHED (option B) clause classifier over the structured seam -- the ingestion default."""
    from rag_wright.models.seam import build_structured

    return LlmBatchClauseClassifier(build_structured(
        model_id, BatchSpanClassification, label="clause_function_classifier.classify_spans"))
