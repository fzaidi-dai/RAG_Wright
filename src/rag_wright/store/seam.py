"""The store seam: the swappable interface every store implementation binds (T13, FR-S.5).

The store is reached only through this interface so it can be swapped without touching capability
code: ArcadeDB is the default (one multi-model store for both the hybrid index and the graph,
FR-S.1), and a separate hybrid vector store (LanceDB) is the eval-gated Phase 1 fallback for the
retrieval leg (GATE-2). The seam is intentionally semantic, not SQL: it exposes schema readiness and
introspection, never a query string, so a second implementation can bind it without inheriting
ArcadeDB's SQL dialect. Write-side and query-side methods are added by the tasks that need them
(T20, T21, T26); T13 defines only the schema-management surface the foundation needs.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from rag_wright.contracts.chunk import ChunkRecord, MetadataValue


@runtime_checkable
class Store(Protocol):
    """A swappable store. The ArcadeDB implementation is the default; a stub proves swappability."""

    def ensure_schema(self) -> None:
        """Create the chunk-record and graph-node types and their indexes, idempotently."""

    def type_names(self) -> set[str]:
        """The names of the types (tables/classes) present in the store."""

    def property_names(self, type_name: str) -> set[str]:
        """The property names declared on `type_name` (empty if the type is absent)."""

    def index_names(self) -> set[str]:
        """The names of the indexes present in the store."""

    def ping(self) -> bool:
        """True if the store is reachable."""

    def close(self) -> None:
        """Release any resources held by the implementation."""

    # --- write-side (T20): the chunk-record write leg. Semantic, not SQL: the seam takes the T3
    # ChunkRecord and each store maps it to its own representation (ArcadeDB decomposes the sparse
    # vector into two parallel arrays; a LanceDB fallback would store it its own way).

    def upsert_chunk(self, record: ChunkRecord) -> None:
        """Write a chunk record, upserting by `chunk_id` (re-write of the same id updates in place)."""

    def get_chunk(self, chunk_id: str) -> Optional[dict]:
        """The stored row for `chunk_id` (store-native fields), or None if absent."""

    def chunk_count(self) -> int:
        """The number of chunk records in the store."""

    # --- query-side (T21): the hybrid retrieval leg. Semantic, not SQL: the seam takes the two
    # query vectors and each store fuses them its own way (ArcadeDB by server-side RRF over its
    # dense/sparse indexes; a LanceDB fallback by its own hybrid query), so FR-C.3 is swappable.

    def hybrid_search(
        self,
        dense_query: list[float],
        sparse_query: dict[int, float],
        *,
        k: int,
        filters: Optional[dict[str, MetadataValue]] = None,
    ) -> list[dict]:
        """Fuse the dense and sparse legs into one ranked candidate list (Reciprocal Rank Fusion),
        honoring equality metadata filters, returning up to `k` rows (each with at least `chunk_id`
        and `source_doc_id`) in ranked order, best first."""
