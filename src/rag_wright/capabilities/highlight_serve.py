"""CU-C2: serve + citation -- turn a `QueryIntent` about a KNOWN contract into a `HighlightResult`.

Given `(query, contract_id, QueryIntent)`, this is the back half of the CUAD front door:

  in-taxonomy, intent=highlight     -> typed within-contract filter -> the span SET (empty => "not present")
  in-taxonomy, intent=extract       -> the set, then field-extract `value_to_extract` from each matched span
  in-taxonomy, intent=discriminate  -> the set; the value_condition discriminator + (b) rerank is STUBBED
                                        (passthrough hook -- the set is small: a handful of same-type spans)
  out-of-taxonomy (in_taxonomy=False) -> semantic fallback over ALL the contract's spans + low-confidence flag

Every returned span carries its exact document location (`doc_start`/`doc_end` from `SpanRecord`, CU-B2) so an
app can highlight it. `structured_factory`/`embedder`/`store` are injected so the routing is tested
hermetically; the field-extraction LLM calls run concurrently (the standing eval/serve concurrency rule).
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel

from rag_wright.contracts.highlight import HighlightResult, HighlightSpan
from rag_wright.contracts.query_intent import QueryIntent
from rag_wright.models.profiles import ModelRole, model_for
from rag_wright.models.seam import build_structured

DEFAULT_FALLBACK_K = 5
_EXTRACT_CONCURRENCY = 8

_EXTRACT_PROMPT = (
    "From the following contract clause, extract {value}. Return ONLY the value as written in the clause; "
    "if the clause does not state it, return null.\n\nClause:\n{text}"
)


class _Extracted(BaseModel):
    value: str | None = None


def _to_span(row: dict, *, confidence: float, extracted_value: str | None = None) -> HighlightSpan:
    return HighlightSpan(
        span_id=row["span_id"],
        contract_id=row["contract_id"],
        function=row.get("function", ""),
        clause_ref=row.get("parent_chunk_id", ""),  # heading/number not extracted yet -> the clause id
        text=row["text"],
        doc_start=row.get("doc_start"),
        doc_end=row.get("doc_end"),
        extracted_value=extracted_value,
        confidence=confidence,
    )


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _field_extract(spans: list[HighlightSpan], value_to_extract: str, factory, model_id: str) -> list[HighlightSpan]:
    """One structured call per matched span (concurrent) to pinpoint `value_to_extract` within it."""

    def _one(sp: HighlightSpan) -> HighlightSpan:
        out = factory(model_id, _Extracted).invoke(
            _EXTRACT_PROMPT.format(value=value_to_extract, text=sp.text)
        )
        return sp.model_copy(update={"extracted_value": out.value})

    async def _run() -> list[HighlightSpan]:
        sem = asyncio.Semaphore(_EXTRACT_CONCURRENCY)

        async def _guarded(sp: HighlightSpan) -> HighlightSpan:
            async with sem:
                return await asyncio.to_thread(_one, sp)

        return await asyncio.gather(*[_guarded(sp) for sp in spans])

    return asyncio.run(_run())


def serve_highlight(
    query: str,
    contract_id: str,
    intent: QueryIntent,
    *,
    store,
    embedder=None,
    structured_factory=build_structured,
    model_id: str | None = None,
    fallback_k: int = DEFAULT_FALLBACK_K,
) -> HighlightResult:
    """Serve a `QueryIntent` against one contract into a cited `HighlightResult`. In-taxonomy routes through
    the typed within-contract filter; out-of-taxonomy falls back to contract-scoped semantic ranking with a
    low-confidence flag. See the module docstring for the four branches."""
    model_id = model_id or model_for(ModelRole.STRUCTURED_REASONING)
    low_confidence = False

    if intent.in_taxonomy:
        rows = store.spans_by_contract(contract_id, intent.clause_types)
        spans = [_to_span(r, confidence=intent.confidence) for r in rows]
        if intent.intent == "extract" and intent.value_to_extract and spans:
            spans = _field_extract(spans, intent.value_to_extract, structured_factory, model_id)
        # intent == "discriminate": the value_condition discriminator + (b) rerank is stubbed -> passthrough.
    else:
        rows = store.all_spans_by_contract(contract_id)
        query_vec = embedder.encode_dense(query) if embedder is not None else None
        if query_vec is not None:
            rows = sorted(rows, key=lambda r: _cosine(query_vec, r["dense"]), reverse=True)
        spans = [_to_span(r, confidence=intent.confidence) for r in rows[:fallback_k]]
        low_confidence = True

    return HighlightResult(
        query=query,
        contract_id=contract_id,
        clause_types=intent.clause_types,
        intent=intent.intent,
        spans=spans,
        present=bool(spans),
        in_taxonomy=intent.in_taxonomy,
        low_confidence=low_confidence,
    )
