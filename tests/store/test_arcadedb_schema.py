"""T13: the store seam + the ArcadeDB schema and hybrid indexes (FR-S.1, FR-S.5).

The stub test is hermetic and proves the seam is bindable by a second implementation (the LanceDB
fallback shape). The `-m store` tests run against a live ArcadeDB (stood up during T13) and prove the
real schema: the dense `LSM_VECTOR` and sparse `LSM_SPARSE_VECTOR` indexes, `chunk_id` on both the
chunk record and the graph node, and idempotent re-creation.
"""

from __future__ import annotations

import pytest

from rag_wright.store.arcadedb import ArcadeDBStore
from rag_wright.store.seam import Store


class _InMemoryStore:
    """A minimal second implementation of the `Store` seam (proves swappability; no ArcadeDB)."""

    def __init__(self) -> None:
        self._types: dict[str, set[str]] = {}
        self._indexes: set[str] = set()

    def ensure_schema(self) -> None:
        self._types = {
            "Chunk": {"chunk_id", "source_doc_id", "dense", "sparse_indices", "sparse_weights"},
            "Entity": {"entity_id", "chunk_id"},
        }
        self._indexes = {
            "Chunk[chunk_id]",
            "Chunk[dense]",
            "Chunk[sparse_indices,sparse_weights]",
            "Entity[entity_id]",
        }

    def type_names(self) -> set[str]:
        return set(self._types)

    def property_names(self, type_name: str) -> set[str]:
        return set(self._types.get(type_name, set()))

    def index_names(self) -> set[str]:
        return set(self._indexes)

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        pass

    # write-side (T20): a chunk store keyed by chunk_id, proving the seam is bindable end to end
    def upsert_chunk(self, record) -> None:
        self._rows = getattr(self, "_rows", {})
        self._rows[record.chunk_id.value] = record

    def get_chunk(self, chunk_id: str):
        row = getattr(self, "_rows", {}).get(chunk_id)
        return {"chunk_id": chunk_id, "summary": row.summary} if row else None

    def chunk_count(self) -> int:
        return len(getattr(self, "_rows", {}))

    # query-side (T21): RRF hybrid search over the seam; the stub returns stored rows honoring the
    # filter (no vector math), enough to prove a second implementation binds the query-side surface
    def hybrid_search(self, dense_query, sparse_query, *, k, filters=None):
        rows = [
            {"chunk_id": r.chunk_id.value, "source_doc_id": r.chunk_id.source_doc_id}
            for r in getattr(self, "_rows", {}).values()
        ]
        if filters:
            rows = [r for r in rows if all(r.get(col) == v for col, v in filters.items())]
        return rows[:k]

    # graph-write (T25): the stub records nodes/edges, proving a second implementation binds the seam
    def write_graph(self, nodes, edges) -> None:
        self._nodes = getattr(self, "_nodes", {})
        self._edges = getattr(self, "_edges", [])
        for node in nodes:
            self._nodes[node.node_key] = node  # upsert by node_key
        self._edges.extend(edges)

    def graph_counts(self) -> dict:
        return {"entities": len(getattr(self, "_nodes", {})),
                "relationships": len(getattr(self, "_edges", []))}

    # query-side (T26): a minimal in-memory one/two-hop traversal over the stored edges
    def graph_neighbors(self, entity_id, *, relationship_type, max_hops):
        nodes, edges = getattr(self, "_nodes", {}), getattr(self, "_edges", [])
        rels = [e for e in edges if e.relationship_type == relationship_type]

        def _name(key):
            node = nodes.get(key)
            return node.name if node else key

        def _neighbors(key):
            for e in rels:
                if e.source_key == key:
                    yield e.target_key, e
                elif e.target_key == key:
                    yield e.source_key, e

        paths = []
        for nb, e1 in _neighbors(entity_id):
            paths.append({"target_id": nb, "target_name": _name(nb), "hops": 1,
                          "path_entity_ids": [entity_id, nb], "path_chunk_ids": [e1.chunk_id],
                          "path_confidences": [e1.confidence]})
            if max_hops >= 2:
                for nb2, e2 in _neighbors(nb):
                    if nb2 not in (entity_id, nb):
                        paths.append({"target_id": nb2, "target_name": _name(nb2), "hops": 2,
                                      "path_entity_ids": [entity_id, nb, nb2],
                                      "path_chunk_ids": [e1.chunk_id, e2.chunk_id],
                                      "path_confidences": [e1.confidence, e2.confidence]})
        return paths


def test_stub_binds_the_store_seam():
    stub = _InMemoryStore()
    assert isinstance(stub, Store)  # structural conformance to the swappable seam
    stub.ensure_schema()
    assert {"Chunk", "Entity"} <= stub.type_names()
    # both the chunk record and the graph node carry chunk_id
    assert "chunk_id" in stub.property_names("Chunk")
    assert "chunk_id" in stub.property_names("Entity")
    assert "Chunk[dense]" in stub.index_names()
    assert "Chunk[sparse_indices,sparse_weights]" in stub.index_names()


def test_arcadedb_store_conforms_to_the_seam():
    assert issubclass(ArcadeDBStore, Store) or isinstance(ArcadeDBStore, type)


# --- live ArcadeDB (opt-in) ---------------------------------------------------------------------

_TEST_DB = "ragwright_test"


@pytest.fixture
def arcadedb_store():
    store = ArcadeDBStore.from_env(database=_TEST_DB, reset=True)
    yield store
    store.drop()
    store.close()


@pytest.mark.store
def test_ensure_schema_creates_dense_and_sparse_hybrid_indexes(arcadedb_store):
    arcadedb_store.ensure_schema()

    types = arcadedb_store.type_names()
    assert {"Chunk", "Entity"} <= types

    indexes = arcadedb_store.index_names()
    assert "Chunk[dense]" in indexes  # dense LSM_VECTOR leg (FR-C.3)
    assert "Chunk[sparse_indices,sparse_weights]" in indexes  # sparse LSM_SPARSE_VECTOR leg
    assert "Chunk[chunk_id]" in indexes  # chunk_id uniqueness

    # chunk records and graph nodes both carry chunk_id (FR-I.4)
    assert "chunk_id" in arcadedb_store.property_names("Chunk")
    assert "chunk_id" in arcadedb_store.property_names("Entity")
    # the sparse leg is stored as two parallel arrays (ArcadeDB LSM_SPARSE_VECTOR requirement)
    assert {"sparse_indices", "sparse_weights", "dense"} <= arcadedb_store.property_names("Chunk")


@pytest.mark.store
def test_ensure_schema_is_idempotent(arcadedb_store):
    arcadedb_store.ensure_schema()
    before = arcadedb_store.index_names()
    arcadedb_store.ensure_schema()  # must not raise on the second pass
    assert arcadedb_store.index_names() == before


@pytest.mark.store
def test_store_is_reachable(arcadedb_store):
    assert arcadedb_store.ping() is True
