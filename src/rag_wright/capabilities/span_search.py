"""Hybrid span search over a workspace's span index (PS-45, engine gap G24).

Every ingested span is embedded dense and sparse (BGE-M3) into the store's `Span` index. This searches it the way
the engine's own retrieval does: the two legs fused by Reciprocal Rank Fusion in the store, plus a DENSE FLOOR
(ADR-0104): RRF weighs the legs equally, so a short query on a common token lets the sparse leg crowd a strong
dense match out of the fused pool. The top `dense_floor` pure-dense spans are unioned in and guaranteed a place in
the result; fusion still decides the order. Each hit carries what a citation needs (the text, the document and its
offsets, pages and box), so it feeds `agenerate_answer` as evidence or a product's highlight directly.

No reranking: a caller that wants one composes `BGEReranker` (or its own ranking) over the hits.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

#: The citation fields read for each hit (not the vectors).
_POSITION_FIELDS = ["span_id", "text", "primary_tag", "document_id", "doc_start", "doc_end", "pages", "bbox"]


class SpanHit(BaseModel):
    """One cited span from `span_search`, best first. `found_by` says which legs found it: `"hybrid"` (the fused
    dense + sparse search) and/or `"dense"` (the dense floor). `bbox` is `(l, t, r, b)` or None."""

    model_config = {"frozen": True}

    span_id: str
    text: str
    document_id: str
    doc_start: Optional[int] = None
    doc_end: Optional[int] = None
    pages: list[int] = []
    bbox: Optional[tuple[float, float, float, float]] = None
    primary_tag: str = ""
    rank: int
    found_by: tuple[str, ...]


def select_with_dense_floor(ranked_ids: list[str], dense_floor: list[str], k: int) -> list[str]:
    """The first `k` of `ranked_ids`, reserving slots so every dense-floor id survives: a non-floor id is skipped
    when the slots left are needed for floor ids not yet taken. A floor id keeps its ranked position where a slot is
    free, so the floor only displaces the weak tail."""
    floor = list(dict.fromkeys(dense_floor))
    result: list[str] = []
    for sid in ranked_ids:
        if len(result) >= k:
            break
        if sid in result:
            continue
        pending = [g for g in floor if g not in result and g != sid]
        if sid in floor or (k - len(result)) > len(pending):
            result.append(sid)
    for g in floor:  # a floor id the ranked pass did not reach
        if len(result) >= k:
            break
        if g not in result:
            result.append(g)
    return result[:k]


def span_search(query: str, *, store: Any, embedder: Any, k: int = 10, documents: Optional[list[str]] = None,
                tag: Optional[str] = None, dense_floor: int = 3) -> list[SpanHit]:
    """The top-`k` cited spans for `query`: fused dense + sparse search, with the top `dense_floor` pure-dense spans
    guaranteed a place (0 turns the floor off). `documents` scopes the search to those source documents in the
    store (`None` = the whole index, `[]` = nothing, no query); `tag` keeps only spans whose primary tag is `tag`
    (the floor ignores it: a semantic match is kept whatever its tag)."""
    from rag_wright.contracts.span import decode_bbox
    from rag_wright.store.arcadedb import SPAN_TYPE

    if k < 1:
        raise ValueError(f"span_search needs k >= 1, got {k}")
    if documents is not None and not documents:
        return []
    dense, sparse = embedder.encode_dense(query), embedder.encode_sparse(query)
    ordered: list[str] = []
    found: dict[str, list[str]] = {}
    for row in store.span_hybrid_search(dense, sparse, k=k, primary_tag=tag, documents=documents):
        if row["span_id"] not in found:
            ordered.append(row["span_id"])
            found[row["span_id"]] = ["hybrid"]
    floor: list[str] = []
    if dense_floor:
        for row in store.span_dense_search(dense, k=dense_floor, documents=documents):
            sid = row["span_id"]
            floor.append(sid)
            if sid not in found:
                ordered.append(sid)
                found[sid] = []
            found[sid].append("dense")
    top = select_with_dense_floor(ordered, floor, k)
    if not top:
        return []
    rows = {r["span_id"]: r for r in store.kg_read(SPAN_TYPE, where={"span_id": top}, fields=_POSITION_FIELDS)}
    return [
        SpanHit(span_id=sid, text=rows[sid].get("text") or "", document_id=rows[sid].get("document_id") or "",
                doc_start=rows[sid].get("doc_start"), doc_end=rows[sid].get("doc_end"),
                pages=list(rows[sid].get("pages") or []), bbox=decode_bbox(rows[sid].get("bbox")),
                primary_tag=rows[sid].get("primary_tag") or "", rank=i, found_by=tuple(found[sid]))
        for i, sid in enumerate((s for s in top if s in rows), 1)
    ]
