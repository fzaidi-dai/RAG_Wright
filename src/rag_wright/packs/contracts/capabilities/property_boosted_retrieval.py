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
    constraint-match score, the query constraints it satisfied, and its 1-based rank. A PURE retrieval contract --
    relevance is a separate judgement (issue 0023): the `typed_property_retrieval` subgraph composes a RankedSpan
    with a `RelevanceVerdict` into a `JudgedSpan`, rather than growing a verdict field here."""

    span_id: str
    text: str
    function: str
    match_score: float
    matched: list[tuple[str, str]]
    rank: int


def _select_with_dense_floor(reranked_ids: list[str], dense_floor: list[str], k: int) -> list[str]:
    """Take the first `k` reranked ids (fusion/constraint order), but RESERVE slots so every dense-floor span
    survives into the returned `k` (issue 0041): a non-floor span is skipped when the remaining slots are needed
    for floor spans not yet included, so a strong dense match the RRF fusion buried is never dropped. A floor span
    keeps its reranked position where a slot is free; constraint-matching spans (high in the reranked order) are
    reached before slots run low, so the floor only displaces the weak, non-matching tail."""
    floor = list(dict.fromkeys(dense_floor))  # dedup, keep dense order
    result: list[str] = []
    for sid in reranked_ids:
        if len(result) >= k:
            break
        if sid in result:
            continue
        pending = [g for g in floor if g not in result and g != sid]
        if sid in floor or (k - len(result)) > len(pending):
            result.append(sid)
        # else: skip this non-floor span, reserving the slot for a still-pending floor span
    for g in floor:  # safety net: any floor span the reranked pass didn't reach (should not happen)
        if len(result) >= k:
            break
        if g not in result:
            result.append(g)
    return result[:k]


def property_boosted_retrieval(
    query: str,
    *,
    store: Any,
    embedder: Any,
    functions: Iterable[str],
    constraints: Iterable[tuple[str, str]],
    k: int = 8,
    pool_k: int = 30,
    documents: list[str] | None = None,
    dense_floor_n: int = 3,
    match_count_fn: Any,
) -> list[RankedSpan]:
    """Retrieve the top-`k` cited spans for `query`, property-boosted by the typed constraints. Pool =
    `span_hybrid_search` (BGE RRF) over each routed function (deduped, BGE order preserved); rerank =
    constraint-match primary + BGE tiebreak via the stable `typed_constraint_match_rank`.

    Issue 0031: `documents` scopes the pool to a workspace's source documents IN THE STORE (`contract_id IN
    [...]`), so out-of-scope spans are never pooled or reranked. `None` = whole index; `[]` = no results.

    Issue 0041: DENSE FLOOR. RRF equal-weights the dense and sparse legs, so a short query on a ubiquitous token
    ('...terms?') lets the sparse leg crowd the strong dense match out of the pool entirely -> the answer span is
    never returned and the product abstains. The top-`dense_floor_n` PURE-DENSE spans are unioned into the pool
    and GUARANTEED into the returned `k` (`_select_with_dense_floor`): fusion still decides order, dense guarantees
    membership. Kept small (default 3) so it recovers the buried dense match without displacing the working
    queries' RRF/constraint results (it only fills non-matching tail slots). `dense_floor_n=0` disables it."""
    constraints = set(constraints)
    dense, sparse = embedder.encode_dense(query), embedder.encode_sparse(query)
    ordered: list[str] = []
    function_of: dict[str, str] = {}
    seen: set[str] = set()
    for f in (list(functions) or [None]):  # None -> no function filter (whole-index pool)
        for h in store.span_hybrid_search(dense, sparse, k=pool_k, function=f, documents=documents):
            sid = h["span_id"]
            if sid not in seen:
                seen.add(sid)
                ordered.append(sid)
                function_of[sid] = h.get("function", "") or (f or "")
    dense_floor: list[str] = []  # issue 0041: the top-N pure-dense spans, guaranteed into the returned k
    if dense_floor_n:
        for h in store.span_dense_search(dense, k=dense_floor_n, documents=documents):
            sid = h["span_id"]
            dense_floor.append(sid)
            if sid not in seen:  # pool it (for props + rerank) if the RRF leg missed it
                seen.add(sid)
                ordered.append(sid)
                function_of[sid] = h.get("function", "")
    if not ordered:
        return []
    from rag_wright.packs.contracts.capabilities.contract_kg_store import ContractKGStore  # EP-REF-1a-ii: typed reads via the domain store
    props = ContractKGStore(store).span_properties(ordered)
    ranked = typed_constraint_match_rank(
        constraints, [(sid, props[sid]) for sid in ordered], match_count_fn=match_count_fn).ranked
    top_ids = _select_with_dense_floor([r.clause_id for r in ranked], dense_floor, k)
    texts = store.span_texts(top_ids)
    score_of = {r.clause_id: r.match_score for r in ranked}
    return [
        RankedSpan(
            span_id=sid, text=texts.get(sid, ""), function=function_of.get(sid, ""),
            match_score=score_of.get(sid, 0.0), matched=sorted(constraints & props.get(sid, set())), rank=i,
        )
        for i, sid in enumerate(top_ids, 1)
    ]


