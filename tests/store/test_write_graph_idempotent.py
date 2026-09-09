"""issue 0029 / ADR-0092: `write_graph` is idempotent on its own -- re-ingesting the same document CONVERGES
rather than accumulating. Nodes stay UPSERT; a `Mentions` edge is created only if absent on (chunk, entity);
a `Relationship` edge only if absent on (source, target, relationship_type, chunk_id). Still ONE atomic
transaction (existence pre-queries, then only-absent CREATEs + node UPSERTs, then execute_transaction).
Hermetic -- `_query` and `_db.execute_transaction` stubbed, no live DB.
"""

from __future__ import annotations

from types import SimpleNamespace

from rag_wright.store.arcadedb import ArcadeDBStore
from rag_wright.store.seam import GraphEdge, GraphNode


def _bare_store(*, chunks: set[str], mentions: set[tuple[str, str]], rels: set[tuple[str, str, str, str]]):
    """A no-connection store whose `_query` answers chunk/Mentions/Relationship existence and whose
    `execute_transaction` is captured. `mentions` = existing (chunk, entity) pairs; `rels` = existing
    (source, target, relationship_type, chunk_id) tuples."""
    tx: list[str] = []
    s = object.__new__(ArcadeDBStore)

    def _query(sql: str):
        if "FROM Chunk" in sql:
            return [{"chunk_id": c} for c in chunks if f"'{c}'" in sql]
        if "FROM Mentions" in sql:
            hit = any(f"outV().chunk_id = '{c}'" in sql and f"inV().entity_id = '{e}'" in sql for c, e in mentions)
            return [{"c": 1 if hit else 0}]
        if "FROM Relationship" in sql:
            hit = any(
                f"relationship_type = '{rt}'" in sql and f"outV().entity_id = '{src}'" in sql
                and f"inV().entity_id = '{tgt}'" in sql and f"chunk_id = '{cid}'" in sql
                for src, tgt, rt, cid in rels
            )
            return [{"c": 1 if hit else 0}]
        return []

    s._query = _query
    s._db = SimpleNamespace(execute_transaction=lambda stmts: tx.extend(stmts))
    return s, tx


_NODES = [
    GraphNode(node_key="UNLINKED:acme", entity_id="", name="Acme Corp",
              entity_type="Organization", confidence="EXTRACTED", chunk_id="c0"),
    GraphNode(node_key="UNLINKED:beta", entity_id="", name="Beta LLC",
              entity_type="Organization", confidence="EXTRACTED", chunk_id="c0"),
]
_EDGES = [GraphEdge(source_key="UNLINKED:acme", target_key="UNLINKED:beta",
                    relationship_type="Contracts With", confidence="EXTRACTED", chunk_id="c0")]


def _counts(tx: list[str]) -> tuple[int, int, int]:
    return (
        sum(s.startswith("UPDATE Entity") for s in tx),
        sum(s.startswith("CREATE EDGE Mentions") for s in tx),
        sum(s.startswith("CREATE EDGE Relationship") for s in tx),
    )


def test_first_ingest_creates_nodes_mentions_and_edge():
    # chunk c0 present; no Mentions, no Relationship yet -> everything is written
    store, tx = _bare_store(chunks={"c0"}, mentions=set(), rels=set())
    store.write_graph(_NODES, _EDGES)
    upserts, mentions, rels = _counts(tx)
    assert (upserts, mentions, rels) == (2, 2, 1)  # 2 node UPSERTs, 2 Mentions, 1 Relationship


def test_reingest_converges_no_duplicate_mention_or_edge():
    # the SAME document again: its Mentions and its Relationship already exist -> only the (idempotent) UPSERTs
    store, tx = _bare_store(
        chunks={"c0"},
        mentions={("c0", "UNLINKED:acme"), ("c0", "UNLINKED:beta")},
        rels={("UNLINKED:acme", "UNLINKED:beta", "Contracts With", "c0")},
    )
    store.write_graph(_NODES, _EDGES)
    upserts, mentions, rels = _counts(tx)
    assert upserts == 2  # nodes are still UPSERT (converge, never duplicate)
    assert mentions == 0 and rels == 0  # nothing appended on the second ingest


def test_same_pair_different_chunk_is_a_distinct_edge():
    # a DIFFERENT contract (chunk c1) between the same two parties: chunk_id is part of the key, so the
    # edge is genuinely new and MUST be written even though (acme, beta, Contracts With) exists for c0
    nodes = [GraphNode(node_key=n.node_key, entity_id=n.entity_id, name=n.name, entity_type=n.entity_type,
                       confidence=n.confidence, chunk_id="c1") for n in _NODES]
    edges = [GraphEdge(source_key="UNLINKED:acme", target_key="UNLINKED:beta",
                       relationship_type="Contracts With", confidence="EXTRACTED", chunk_id="c1")]
    store, tx = _bare_store(
        chunks={"c1"},
        mentions=set(),
        rels={("UNLINKED:acme", "UNLINKED:beta", "Contracts With", "c0")},  # only the c0 edge exists
    )
    store.write_graph(nodes, edges)
    _, _, rels = _counts(tx)
    assert rels == 1  # the c1 edge is distinct provenance and is created


def test_within_batch_duplicate_edge_created_once():
    # the same absent edge twice in one call -> only one CREATE (fully convergent, not just cross-call)
    store, tx = _bare_store(chunks={"c0"}, mentions=set(), rels=set())
    store.write_graph(_NODES, _EDGES + list(_EDGES))
    _, _, rels = _counts(tx)
    assert rels == 1


def test_missing_chunk_writes_no_mention_but_still_writes_edge():
    # chunk not yet indexed -> no Mentions edge (unchanged pre-existing behavior), edge still written
    store, tx = _bare_store(chunks=set(), mentions=set(), rels=set())
    store.write_graph(_NODES, _EDGES)
    upserts, mentions, rels = _counts(tx)
    assert (upserts, mentions, rels) == (2, 0, 1)
