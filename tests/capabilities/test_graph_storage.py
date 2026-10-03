"""Graph storage (T25, FR-I.4/FR-I.5): write resolved entities + relationships into the store, gated.

Hermetic tests over a fake store prove the ResolutionResult -> nodes/edges mapping (linked vs unlinked
node keys, ref-only endpoint nodes) and the content-hash gate. The live `-m store` test writes a real
chunk + graph into ArcadeDB in one transaction and checks the nodes, the relationship edge, the
chunk->entity Mentions edges, and that an unchanged re-run does no graph work.
"""

from __future__ import annotations

import pytest

from rag_wright.capabilities.entity_resolution import (
    ResolutionResult,
    ResolvedEntity,
    ResolvedRelationship,
)
from rag_wright.capabilities.graph_storage import GraphWriter, to_graph
from rag_wright.contracts.chunk import BGE_M3_DENSE_DIM, ChunkRecord
from rag_wright.contracts.identifiers import ChunkId
from rag_wright.contracts.provenance import ConfidenceTag

_CID = ChunkId.of("docA", 0, "chunk text")
_X = ConfidenceTag.EXTRACTED


def _entity(key, rep, entity_id) -> ResolvedEntity:
    return ResolvedEntity(key=key, representative=rep, entity_type="Organization",
                          entity_id=entity_id, confidence=_X, chunk_ids=[_CID.value])


def _rel(source_ref, source_id, target_ref, target_id) -> ResolvedRelationship:
    return ResolvedRelationship(source_ref=source_ref, target_ref=target_ref, source_id=source_id,
                                target_id=target_id, relationship_type="Contracts With",
                                confidence=_X, chunk_id=_CID.value)


class _FakeStore:
    def __init__(self) -> None:
        self.nodes: dict[str, object] = {}
        self.edges: list = []
        self.write_calls = 0

    def write_graph(self, nodes, edges) -> None:
        self.write_calls += 1
        for node in nodes:
            self.nodes[node.node_key] = node
        self.edges.extend(edges)

    def graph_counts(self) -> dict:
        return {"entities": len(self.nodes), "relationships": len(self.edges)}


# --- mapping -------------------------------------------------------------------------------------


def test_linked_and_unlinked_entities_map_to_node_keys():
    resolution = ResolutionResult(
        entities=[_entity("acme", "Acme Corp", "0000000001"), _entity("private co", "Private Co", None)],
        relationships=[],
    )
    nodes, edges = to_graph(resolution)

    by_key = {n.node_key: n for n in nodes}
    assert "0000000001" in by_key  # linked -> CIK node key
    assert by_key["0000000001"].entity_id == "0000000001"
    assert "UNLINKED:private co" in by_key  # unlinked -> surrogate key
    assert by_key["UNLINKED:private co"].entity_id == ""
    assert all(n.name and n.entity_type and n.confidence for n in nodes)  # relationship layer only


def test_relationship_maps_to_an_edge_between_the_resolved_node_keys():
    resolution = ResolutionResult(
        entities=[_entity("acme", "Acme Corp", "0000000001"), _entity("beta", "Beta LLC", "0000000002")],
        relationships=[_rel("Acme Corp", "0000000001", "Beta LLC", "0000000002")],
    )
    nodes, edges = to_graph(resolution)
    assert len(edges) == 1
    assert (edges[0].source_key, edges[0].target_key) == ("0000000001", "0000000002")
    assert edges[0].relationship_type == "Contracts With"


def test_ref_only_endpoint_gets_a_node_so_the_edge_connects():
    # a relationship whose endpoints have no standalone entity still yields endpoint nodes
    resolution = ResolutionResult(
        entities=[], relationships=[_rel("Acme Corp", None, "Beta LLC", None)]
    )
    nodes, edges = to_graph(resolution)
    keys = {n.node_key for n in nodes}
    assert keys == {"UNLINKED:acme", "UNLINKED:beta"}  # both endpoints materialized
    assert (edges[0].source_key, edges[0].target_key) == ("UNLINKED:acme", "UNLINKED:beta")


# --- content-hash gate ---------------------------------------------------------------------------


def test_writes_then_skips_an_unchanged_document(tmp_path):
    store = _FakeStore()
    writer = GraphWriter(store, checkpoint_dir=tmp_path)
    resolution = ResolutionResult(
        entities=[_entity("acme", "Acme Corp", "0000000001"), _entity("beta", "Beta LLC", "0000000002")],
        relationships=[_rel("Acme Corp", "0000000001", "Beta LLC", "0000000002")],
    )

    first = writer.write_document("docA", "hash1", resolution)
    assert first.status == "written" and first.node_count == 2 and first.edge_count == 1

    second = writer.write_document("docA", "hash1", resolution)  # unchanged -> no graph work
    assert second.status == "skipped"
    assert store.write_calls == 1  # the store was written exactly once


def test_changed_content_hash_rewrites(tmp_path):
    store = _FakeStore()
    writer = GraphWriter(store, checkpoint_dir=tmp_path)
    resolution = ResolutionResult(entities=[_entity("acme", "Acme Corp", "0000000001")], relationships=[])

    writer.write_document("docA", "hash1", resolution)
    result = writer.write_document("docA", "hash2", resolution)  # content changed
    assert result.status == "written"
    assert store.write_calls == 2


# --- live ArcadeDB (opt-in): the real transaction + edges ----------------------------------------

_DB = "ragwright_graph_storage_test"


@pytest.fixture
def live_store():
    from rag_wright.store.arcadedb import ArcadeDBStore

    store = ArcadeDBStore.from_env(database=_DB, reset=True)
    store.ensure_schema()
    # a chunk must exist for the chunk->entity Mentions edge (pipeline order: chunk write before graph)
    store.upsert_chunk(ChunkRecord(chunk_id=_CID, summary="s", dense_vector=[0.0] * BGE_M3_DENSE_DIM,
                                   sparse_vector={1: 0.5}))
    yield store
    store.drop()
    store.close()


@pytest.mark.store
def test_live_graph_write_creates_nodes_edges_and_mentions_then_gates(live_store, tmp_path):
    resolution = ResolutionResult(
        entities=[_entity("acme", "Acme Corp", "0000000001"), _entity("beta", "Beta LLC", "0000000002")],
        relationships=[_rel("Acme Corp", "0000000001", "Beta LLC", "0000000002")],
    )
    writer = GraphWriter(live_store, checkpoint_dir=tmp_path)

    result = writer.write_document("docA", "hash1", resolution)
    assert result.status == "written"
    assert live_store.graph_counts() == {"entities": 2, "relationships": 1}
    mentions = live_store._db.query("sql", "SELECT count(*) AS n FROM Mentions")
    assert int(mentions[0]["n"]) == 2  # each entity connected to its source chunk

    again = writer.write_document("docA", "hash1", resolution)  # unchanged -> no graph work
    assert again.status == "skipped"
    assert live_store.graph_counts() == {"entities": 2, "relationships": 1}  # no duplicates
