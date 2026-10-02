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

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

from rag_wright.contracts.chunk import ChunkRecord, MetadataValue


@dataclass(frozen=True)
class GraphNode:
    """A graph entity node to write (T25). `node_key` is the vertex identity (the resolver's canonical id when
    linked, an `UNLINKED:<key>` surrogate otherwise); `entity_id` is the canonical id, or empty when unlinked."""

    node_key: str
    entity_id: str
    name: str
    entity_type: str
    confidence: str
    chunk_id: str


@dataclass(frozen=True)
class GraphEdge:
    """A relationship edge between two entity nodes (by `node_key`), carrying provenance + confidence."""

    source_key: str
    target_key: str
    relationship_type: str
    confidence: str
    chunk_id: str


@dataclass(frozen=True)
class KgNode:
    """A typed KG node to upsert (DD-1b, ADR-0117): `type` is the vertex type, `key_field` the identity field to
    upsert on, `props` the fields (including `key_field`) as DOMAIN-NATIVE values. The store encodes each prop by its
    pack-declared storage type -- the caller never serializes to the store's wire format."""

    type: str
    key_field: str
    props: dict[str, object]


@dataclass(frozen=True)
class KgEdge:
    """A typed KG edge to create between two nodes identified by (type, key_field, key). `props` are native values
    (edge properties are type-driven: edges declare no storage schema)."""

    type: str
    from_type: str
    from_key_field: str
    from_key: object
    to_type: str
    to_key_field: str
    to_key: object
    props: dict[str, object]


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

    # --- graph-write (T25): the knowledge-graph leg. Semantic, not SQL: the seam takes entity nodes
    # and relationship edges and each store writes them its own way (ArcadeDB as vertices/edges in
    # one transaction; a fallback store however it models a graph). Nodes/edges carry chunk_id (FR-I.4).

    def write_graph(self, nodes: list[GraphNode], edges: list[GraphEdge]) -> None:
        """Write entity nodes (upsert by `node_key`) and relationship edges between them in ONE
        transaction (FR-S.1: a chunk and its entities land together), connecting each entity to its
        source chunk. Nodes and edges carry `chunk_id` and confidence (FR-I.4)."""

    def graph_counts(self) -> dict[str, int]:
        """Counts for introspection/tests: `{'entities': n, 'relationships': m}`."""

    def entities_by_name(self, name: str) -> list[dict]:
        """Resolve a party NAME to its graph entities (issue 0030): the first step before
        `graph_neighbors`/`graph_query`, which take a `start_entity_id` (an exact node key) and cannot be
        reached from a name otherwise. Returns `[{entity_id, name, entity_type}]` for every stored entity
        whose name normalizes to the same clustering key as `name`, via the SAME `normalize_entity_name`
        the ingestion side uses to merge 'Acme Corp' / 'Acme Corporation' / 'ACME, Inc.' into one entity.
        Normalization is the engine's rule and is applied HERE, so a caller never re-implements it (a raw
        or an already-normalized name both work; the normalization is idempotent). `entity_id` is exactly
        the node key `graph_neighbors`/`graph_query` take as `start_entity_id`. Empty list on no match."""

    # --- graph-query (T26): relationship traversal. Semantic, not SQL: returns store-agnostic path
    # rows (target + the entity_ids/chunk_ids/confidences along the path) so the capability can shape
    # the cited evidence. Confidence is SURFACED on every path, not filtered on (FR-C.5/FR-Q.3).

    def graph_neighbors(
        self, entity_id: str, *, relationship_type: str, max_hops: int, documents: Optional[list[str]] = None
    ) -> list[dict]:
        """Traverse `relationship_type` edges from the start entity up to `max_hops`, returning one row
        per reached entity+path: `target_id`, `target_name`, `path_entity_ids`, `path_chunk_ids`,
        `path_confidences`, `hops`. Every edge is surfaced regardless of confidence (T26 does not gate).
        `documents` (issue 0031): scope the traversal to those source documents -- EVERY edge on a path must
        belong to one of them; `None` = the whole graph, `[]` = scope-to-nothing (no rows)."""

    # --- generic typed-node read (DD-1a, ADR-0117): the backend-agnostic primitive a domain store extension
    # delegates to, so a domain pack never emits store-native SQL. Semantic, not SQL.

    def kg_read(
        self,
        node_type: str,
        *,
        where: Optional[dict[str, object]] = None,
        fields: Optional[list[str]] = None,
        distinct: Optional[str] = None,
        order_by: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[dict]:
        """Read typed nodes of `node_type`. `where` maps a field to a scalar (equality) or a list (membership);
        a list value that is EMPTY means scope-to-nothing and returns `[]` without a query. `distinct` returns the
        distinct values of one field; `fields=None` returns all fields. Equality/membership clauses are AND-ed."""

    def kg_write(self, nodes: list["KgNode"], edges: "Iterable[KgEdge]" = ()) -> None:
        """Upsert typed `nodes` (by each node's `key_field`) then create typed `edges` (FROM/TO by node key), ALL in
        ONE transaction, nodes first so endpoints exist. The caller passes DOMAIN-NATIVE values; the store owns all
        wire encoding, driven by each node type's pack-declared property storage type. Empty input is a no-op."""
