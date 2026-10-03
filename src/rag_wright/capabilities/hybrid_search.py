"""Hybrid search (FR-C.3, FR-Q.1): server-side RRF fusion of the dense and sparse legs.

Given a natural-language query, embed it once with the shared BGE-M3 embedder seam (T19) — dense and
sparse over the same query text — and hand both query vectors to the store's server-side hybrid
search (the T13 seam), which fuses the dense `vector.neighbors` leg and the sparse
`vector.sparseNeighbors` leg by Reciprocal Rank Fusion in ArcadeDB (proven end to end at T14) into one
ranked candidate list, honoring metadata filters. The fusion runs in the store, so there is no
cross-store join and no client-side re-ranking here; this is the query-side entry point the compiler
binds under FR-C.3. Reranking (the precision gate, FR-C.4/T22) consumes this list downstream.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from rag_wright.capabilities.embedding import Embedder
from rag_wright.contracts.chunk import MetadataValue
from rag_wright.store.seam import Store

DEFAULT_K = 10  # candidate-list depth; the eval and the reranker (T22) can override per call


class Candidate(BaseModel):
    """One fused retrieval candidate: enough to cite it (`chunk_id`) and filter by source."""

    model_config = {"frozen": True}

    chunk_id: str
    source_doc_id: str


class HybridSearchResult(BaseModel):
    """The hybrid-search capability's output: the RRF-ranked candidate list for a query (FR-C.3)."""

    model_config = {"frozen": True}

    query: str
    candidates: list[Candidate]  # RRF-ranked, best first


def hybrid_search(
    query: str,
    *,
    store: Store,
    embedder: Embedder,
    k: int = DEFAULT_K,
    filters: Optional[dict[str, MetadataValue]] = None,
) -> HybridSearchResult:
    """Embed the query and return the RRF-fused, filter-honoring top-`k` candidate list.

    The query is embedded once — dense and sparse over the same query text (a query has no
    summary/full-text split, unlike a chunk) — through the injected `Embedder` seam, then fused
    server-side by the store. The server's rank order is preserved; this capability does not re-rank.
    """
    dense_query = embedder.encode_dense(query)
    sparse_query = embedder.encode_sparse(query)
    rows = store.hybrid_search(dense_query, sparse_query, k=k, filters=filters)
    candidates = [
        Candidate(chunk_id=row["chunk_id"], source_doc_id=row["source_doc_id"]) for row in rows
    ]
    return HybridSearchResult(query=query, candidates=candidates)


