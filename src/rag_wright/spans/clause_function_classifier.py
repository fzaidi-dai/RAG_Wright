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
import re
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

    async def _aclassify_raw(self, chunk_text: str, span_texts: list[str],
                             *, sem: asyncio.Semaphore | None = None) -> list[list[RawScore]]:
        """ASYNC-B1 (ADR-0057): the async twin of `_classify_raw`. Sub-batches run concurrently via
        `asyncio.gather` bounded by a `Semaphore` (native form of the sync thread pool); each `.ainvoke` carries
        the true wall-clock deadline. A failed sub-batch -- including a `ModelCallTimeout` (an Exception, so it is
        caught here) -- leaves its spans empty: the degrade path stays reachable and the node never raises.

        CLASSIFY-CONCURRENCY-1: a caller classifying MANY chunks concurrently passes ONE shared `sem`, so the total
        in-flight sub-batch calls across all chunks are bounded by a single deliberate knob (else each chunk got
        its own `_SUBBATCH_CONCURRENCY` budget). The sem is acquired only at the leaf `.ainvoke` -- never held
        across another acquire -- so nesting the chunk gather over it cannot deadlock."""
        if not span_texts:
            return []
        subs = _subs(span_texts)
        sem = sem if sem is not None else asyncio.Semaphore(_SUBBATCH_CONCURRENCY)

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

    async def aclassify_spans(self, chunk_text: str, span_texts: list[str],
                              *, sem: asyncio.Semaphore | None = None) -> list[list[FunctionScore]]:
        return [_to_scores(raws) for raws in await self._aclassify_raw(chunk_text, span_texts, sem=sem)]

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


# --- issue 0005 / route (b): CLIENT-SIDE free-text tag classification (no server-side guided decoding) ---------
#
# The classifier schemas are nested list[BaseModel] (BatchSpanClassification.spans -> SpanFunctions.functions ->
# RawScore), so `build_tag_structured` (FLAT only) can't serve them. Server-side guided decoding
# (`build_structured`) runs away on self-hosted Granite (profiles.py: no `client_side_structured` escape) -- the
# ADR-0058 boundary-call failure at a new call site. So, like `answer_generator.parse_tagged_answer`, the
# classifier uses its OWN compact free-text tag format that FLATTENS the nesting into the tag BODY, parsed
# CLIENT-SIDE into the same Pydantic contract. `function` stays raw -- the downstream categorize/floor/cap cleans
# a stray, so a malformed token never crashes a sub-batch.

_BATCH_TAG_INSTRUCTIONS = (
    "\n\nReturn ONLY the classification as tagged lines -- ONE per span you assign a function to (OMIT any span "
    "with no function). Between the tags put the applicable function labels PRIMARY-FIRST as `Label:confidence` "
    "(confidence = high, medium, or low), comma-separated. For a real clause type NOT in the list above, use "
    "`OTHER:confidence:short_name`. Use ONLY the [n] span indices shown. Example:\n"
    '<span index="0">Cap On Liability:high, Indemnification:medium</span>\n'
    '<span index="3">Governing Law:high</span>'
)
_CLAUSE_TAG_INSTRUCTIONS = (
    "\n\nReturn ONLY the applicable function labels PRIMARY-FIRST as `Label:confidence` (confidence = high, "
    "medium, or low), comma-separated, between <functions></functions> tags; for a real clause type NOT in the "
    "list above, use `OTHER:confidence:short_name`. If none apply, return `<functions></functions>`. Example:\n"
    "<functions>Cap On Liability:high, Indemnification:low</functions>"
)

_SPAN_TAG_RE = re.compile(r'<span\s+index="?(\d+)"?\s*>(.*?)</span>', re.DOTALL | re.IGNORECASE)
_FUNCTIONS_TAG_RE = re.compile(r"<functions>(.*?)</functions>", re.DOTALL | re.IGNORECASE)


def _parse_score_item(item: str) -> RawScore | None:
    """One `Label:confidence` (or `OTHER:confidence:other_label`) token -> a RawScore, kept RAW."""
    parts = [p.strip() for p in item.split(":")]
    if not parts or not parts[0]:
        return None
    if parts[0].upper() == "OTHER":
        return RawScore(function="OTHER", confidence=parts[1] if len(parts) > 1 else "low",
                        other_label=parts[2] if len(parts) > 2 else "")
    if len(parts) == 1:
        return RawScore(function=parts[0], confidence="low")  # no confidence given -> lowest (dropped by floor)
    return RawScore(function=":".join(parts[:-1]), confidence=parts[-1])  # rejoin a label that itself had a colon


def _parse_scores(body: str) -> list[RawScore]:
    return [rs for item in re.split(r"[,\n]", body) if (rs := _parse_score_item(item.strip())) is not None]


def parse_batch_span_tags(text: str) -> BatchSpanClassification:
    """CLIENT-SIDE parse of the batched free-text tags -> BatchSpanClassification. Sparse (a no-function span is
    absent) and aligned by the EXPLICIT index in each tag, matching the schema's span_index contract."""
    spans = [SpanFunctions(span_index=int(m.group(1)), functions=scores)
             for m in _SPAN_TAG_RE.finditer(text) if (scores := _parse_scores(m.group(2)))]
    return BatchSpanClassification(spans=spans)


def parse_clause_function_tags(text: str) -> ClauseFunctionClassification:
    """CLIENT-SIDE parse of the single-clause free-text tags -> ClauseFunctionClassification."""
    m = _FUNCTIONS_TAG_RE.search(text)
    return ClauseFunctionClassification(functions=_parse_scores(m.group(1)) if m else [])


class _TagClassifierRunnable:
    """A `build_structured`-shaped runnable (`.invoke`/`.ainvoke(prompt) -> the Pydantic contract`) that drives the
    classifier CLIENT-SIDE: a plain free-text call (NO server guided decoding) whose tagged output is parsed by
    `parse`. `.ainvoke` streams via `astream_text` (true wall-clock deadline + the stage `label`, issue 0005)."""

    def __init__(self, model_id: str, *, instructions: str, parse: Any, label: str, max_tokens: int = 2048) -> None:
        self._model_id = model_id
        self._instructions = instructions
        self._parse = parse
        self._label = label
        self._max_tokens = max_tokens

    def invoke(self, prompt: Any, config: Any = None) -> Any:  # config accepted for runnable-compat, unused
        from rag_wright.models.seam import build_model

        text = build_model(self._model_id, max_tokens=self._max_tokens).invoke(prompt + self._instructions).content
        return self._parse(str(text))

    async def ainvoke(self, prompt: Any, config: Any = None) -> Any:
        from rag_wright.models.seam import astream_text

        text = await astream_text(self._model_id, prompt + self._instructions,
                                  max_tokens=self._max_tokens, label=self._label)
        return self._parse(text)


def production_llm_clause_classifier(model_id: str) -> LlmClauseClassifier:
    """Wire the single-clause classifier over CLIENT-SIDE free-text tag parse (issue 0005: no server-side guided
    decoding, which runs away on self-hosted Granite). Same `ClauseFunctionClassification` contract."""
    return LlmClauseClassifier(_TagClassifierRunnable(
        model_id, instructions=_CLAUSE_TAG_INSTRUCTIONS, parse=parse_clause_function_tags,
        label="clause_function_classifier.classify"))


def production_batch_clause_classifier(model_id: str) -> LlmBatchClauseClassifier:
    """Wire the BATCHED (option B) classifier -- the ingestion default -- over CLIENT-SIDE free-text tag parse
    (issue 0005). Same `BatchSpanClassification` contract; parsed by `parse_batch_span_tags`."""
    return LlmBatchClauseClassifier(_TagClassifierRunnable(
        model_id, instructions=_BATCH_TAG_INSTRUCTIONS, parse=parse_batch_span_tags,
        label="clause_function_classifier.classify_spans"))


# --- T55 / SETFIT-SEG-1: trained SetFit soft-tagger (replaces the LLM classifier for ingestion latency) ----------
# Behind the SAME ClauseFunctionClassifier seam as LlmBatchClauseClassifier / LegalBertClauseAdapter -- a new
# IMPLEMENTATION of the already-registered `clause_function_classification` capability, no contract/API change.
# Function is a SOFT tag (ADR-0047), so emitting multiple tags per span is intended.
class SetFitClauseAdapter:
    """In-process ENSEMBLE of trained SetFit models. Each model on disk is a Sentence-Transformer body
    (`model.safetensors` + configs) + a joblib-pickled sklearn head (`model_head.pkl`), so inference needs only
    sentence-transformers + scikit-learn + joblib -- NO `setfit` dependency. `classify_spans` encodes every span
    with each body, AVERAGES the per-class probabilities across the ensemble, and emits the top-k labels above
    `threshold` as `FunctionScore` soft tags (primary-first). Span-level (ignores `chunk_text`, like
    `LegalBertClauseAdapter`). Load-don't-retrain from local checkpoints; milliseconds/span, no network hop."""

    def __init__(self, model_dirs, *, top_k: int = 3, threshold: float = 0.0,
                 hi: float = 0.6, mid: float = 0.3, device: str | None = None, batch_size: int = 32) -> None:
        # DEFAULT = the finalized operating point: pure avg-prob TOP-3 (threshold 0.0) -> ~3.0 tags/span, the
        # validated 50/52 classes >0.65 recall. A higher threshold trims tags but drops recall (e.g. 0.10 -> ~2.0
        # tags, ~47/52); tune via RAG_SETFIT_THRESHOLD / RAG_SETFIT_TOPK only with a re-measured operating point.
        import json
        from pathlib import Path

        import joblib
        import numpy as np
        from sentence_transformers import SentenceTransformer

        self._np = np
        self._top_k, self._threshold, self._hi, self._mid, self._batch = top_k, threshold, hi, mid, batch_size
        self._models: list[tuple] = []
        for d in model_dirs:
            d = Path(d)
            cfg = json.loads((d / "config_setfit.json").read_text())
            body = SentenceTransformer(str(d), device=device)
            head = joblib.load(d / "model_head.pkl")
            self._models.append((body, head, bool(cfg.get("normalize_embeddings", False)),
                                 [str(c) for c in head.classes_]))
        if not self._models:
            raise ValueError("SetFitClauseAdapter needs at least one model dir")
        self._labels = sorted({lab for *_, cols in self._models for lab in cols})  # union label space (robust)
        self._lab_idx = {lab: i for i, lab in enumerate(self._labels)}

    def classify(self, clause_text: str) -> list[FunctionScore]:
        return self.classify_spans("", [clause_text])[0]

    async def aclassify_spans(self, chunk_text: str, span_texts: list[str],
                              *, sem: asyncio.Semaphore | None = None) -> list[list[FunctionScore]]:
        # the ingestion pipeline calls aclassify_spans under a shared concurrency bound. SetFit is CPU-bound and
        # in-process, so run the sync encode+predict off the event loop in a thread; the sem bounds in-flight work.
        if sem is None:
            return await asyncio.to_thread(self.classify_spans, chunk_text, span_texts)
        async with sem:
            return await asyncio.to_thread(self.classify_spans, chunk_text, span_texts)

    def classify_spans(self, chunk_text: str, span_texts: list[str]) -> list[list[FunctionScore]]:  # noqa: ARG002
        if not span_texts:
            return []
        np = self._np
        agg = np.zeros((len(span_texts), len(self._labels)))
        for body, head, norm, cols in self._models:
            proba = np.asarray(head.predict_proba(
                body.encode(list(span_texts), normalize_embeddings=norm, batch_size=self._batch)), dtype=float)
            for j, c in enumerate(cols):
                idx = self._lab_idx.get(c)
                if idx is not None:
                    agg[:, idx] += proba[:, j]
        agg /= len(self._models)
        out: list[list[FunctionScore]] = []
        for row in agg:
            scores: list[FunctionScore] = []
            for j in np.argsort(-row)[: self._top_k]:
                p = float(row[j])
                if p < self._threshold:
                    break
                canon = canonical_function(self._labels[int(j)])
                if not canon:
                    continue
                conf = (FunctionConfidence.HIGH if p >= self._hi
                        else FunctionConfidence.MEDIUM if p >= self._mid else FunctionConfidence.LOW)
                scores.append(FunctionScore(function=canon, confidence=conf))
            out.append(scores)
        return out


def production_setfit_clause_classifier(model_root: str | None = None, **kwargs) -> SetFitClauseAdapter:
    """Wire the ENSEMBLE SetFit soft-tagger -- the ingestion default (replaces the LLM classifier for latency,
    T55/SETFIT-SEG-1). Loads the 3 finalized checkpoints (LegalBERT + BGE-large + MPNet) from `model_root`
    (env RAG_SETFIT_CLAUSE_DIR; default data/models/setfit_clause). In-process, no network hop, no `setfit` dep.
    Tunables: RAG_SETFIT_TOPK, RAG_SETFIT_THRESHOLD, RAG_SETFIT_DEVICE."""
    import os
    from pathlib import Path

    root = Path(model_root or os.getenv("RAG_SETFIT_CLAUSE_DIR", "data/models/setfit_clause"))
    subdirs = [root / n for n in ("cap128b_legalbert", "cap128b_bge", "cap128b_mpnet")]
    present = [d for d in subdirs if (d / "model_head.pkl").exists()]
    if not present:
        raise FileNotFoundError(
            f"No SetFit clause checkpoints under {root} (expected cap128b_legalbert/bge/mpnet). "
            "Download them from the model store, or set RAG_FUNCTION_CLASSIFIER=llm to use the LLM classifier.")
    return SetFitClauseAdapter(
        present,
        top_k=int(os.getenv("RAG_SETFIT_TOPK", str(kwargs.pop("top_k", 3)))),
        threshold=float(os.getenv("RAG_SETFIT_THRESHOLD", str(kwargs.pop("threshold", 0.0)))),
        device=os.getenv("RAG_SETFIT_DEVICE") or kwargs.pop("device", None),
        **kwargs)
