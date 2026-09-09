"""SPAN-CLAUSE-RERANK (b) (FR-Q, ADR-0033): property-boosted typed retrieval over the CUAD-full KG.

The KG-5 V4 adopted shape, bound to CUAD-full via the operative-span `edge.span_id` join (SPAN-CLAUSE-RERANK):
  BGE base pool (`store.span_hybrid_search`, function-filtered, bounded) -> join each span to its clause's
  typed (dimension, value) props (`store.span_properties`, the edge.span_id join) -> `typed_constraint_match_rank`
  (STABLE sort: constraint-match count primary, so the BGE pool order is the tiebreak) -> top-k cited spans.

The routed `functions` and typed `constraints` come from the query front-door (the MS1-6 A100 path: LegalBERT +
granite routing, granite constraint-extraction); they are passed in so this capability is a pure store+embedder
composition, hermetically testable with fakes. `store` / `embedder` are the seams (local or the A100 adapters).
"""

from __future__ import annotations

from typing import Any, Iterable

from pydantic import BaseModel

from rag_wright.capabilities.retrieval_core import typed_constraint_match_rank


class RankedSpan(BaseModel):
    """One property-boosted, cited retrieval result (FR-Q.6): the span citation + text, its function, the
    constraint-match score, the query constraints it satisfied, and its 1-based rank.

    `retrieval_score` (issue 0023) is the BGE RRF fused relevance that ORDERED the pool (higher = better), carried
    alongside `match_score` (a COUNT of satisfied typed constraints) so a caller can apply its OWN relevance floor
    -- e.g. grade a sweep into matched / possible / not_found. The engine does NOT pick the threshold. It is None
    only when the backend did not surface a fused score."""

    span_id: str
    text: str
    function: str
    match_score: float
    matched: list[tuple[str, str]]
    rank: int
    retrieval_score: float | None = None


def property_boosted_retrieval(
    query: str,
    *,
    store: Any,
    embedder: Any,
    functions: Iterable[str],
    constraints: Iterable[tuple[str, str]],
    k: int = 8,
    pool_k: int = 30,
) -> list[RankedSpan]:
    """Retrieve the top-`k` cited spans for `query`, property-boosted by the typed constraints. Pool =
    `span_hybrid_search` (BGE RRF) over each routed function (deduped, BGE order preserved); rerank =
    constraint-match primary + BGE tiebreak via the stable `typed_constraint_match_rank`."""
    constraints = set(constraints)
    dense, sparse = embedder.encode_dense(query), embedder.encode_sparse(query)
    ordered: list[str] = []
    function_of: dict[str, str] = {}
    retrieval_score_of: dict[str, float | None] = {}  # issue 0023: the fused relevance that ordered the pool
    seen: set[str] = set()
    for f in (list(functions) or [None]):  # None -> no function filter (whole-index pool)
        for h in store.span_hybrid_search(dense, sparse, k=pool_k, function=f):
            sid = h["span_id"]
            if sid not in seen:
                seen.add(sid)
                ordered.append(sid)
                function_of[sid] = h.get("function", "") or (f or "")
                retrieval_score_of[sid] = h.get("score")  # keep the highest-ranked leg's fused score for the span
    if not ordered:
        return []
    props = store.span_properties(ordered)
    ranked = typed_constraint_match_rank(constraints, [(sid, props[sid]) for sid in ordered]).ranked
    top_ids = [r.clause_id for r in ranked[:k]]
    texts = store.span_texts(top_ids)
    score_of = {r.clause_id: r.match_score for r in ranked}
    return [
        RankedSpan(
            span_id=sid, text=texts.get(sid, ""), function=function_of.get(sid, ""),
            match_score=score_of.get(sid, 0.0), matched=sorted(constraints & props.get(sid, set())), rank=i,
            retrieval_score=retrieval_score_of.get(sid),
        )
        for i, sid in enumerate(top_ids, 1)
    ]


def register_property_boosted_retrieval(registry) -> None:
    """Register `property_boosted_retrieval` (function; ADR-0033 typed property-boost rerank over CUAD-full)."""
    registry.register(
        "property_boosted_retrieval",
        contract=RankedSpan,
        kind="function",
        display_name="Property-boosted typed retrieval",
    )
