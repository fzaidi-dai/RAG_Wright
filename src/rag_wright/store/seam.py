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

from typing import Protocol, runtime_checkable


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
