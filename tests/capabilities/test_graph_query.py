"""Graph query (T26, FR-C.5/FR-Q.3): cited relational/multi-hop answers as evidence.

Hermetic tests over a canned store prove the capability shapes the traversal rows into cited evidence
(chunk_ids, entity_ids, confidence tags), passes the relationship type + hops through, surfaces (does
not filter) confidence, and returns evidence — not a ranked list. The live `-m store` test traverses a
real graph in ArcadeDB, one hop and two hops.
"""

from __future__ import annotations

import pytest

from rag_wright.capabilities.graph_query import GraphAnswer, graph_query


class _FakeStore:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.calls: list[dict] = []

    def graph_neighbors(self, entity_id, *, relationship_type, max_hops, documents=None):
        self.calls.append({"entity_id": entity_id, "relationship_type": relationship_type,
                           "max_hops": max_hops})
        return self._rows


def _row(target_id, name, hops, path_ids, chunk_ids, confidences):
    return {"target_id": target_id, "target_name": name, "hops": hops, "path_entity_ids": path_ids,
            "path_chunk_ids": chunk_ids, "path_confidences": confidences}


# --- hermetic ------------------------------------------------------------------------------------


def test_shapes_traversal_rows_into_cited_evidence():
    rows = [_row("0000000002", "Beta", 1, ["0000000001", "0000000002"], ["docA:0:h"], ["EXTRACTED"])]
    store = _FakeStore(rows)

    answer = graph_query("0000000001", store=store,
                         relationship_type="related_to", max_hops=1)  # graph_query takes ANY edge-type string

    assert isinstance(answer, GraphAnswer)
    assert store.calls[0] == {"entity_id": "0000000001", "relationship_type": "related_to",
                              "max_hops": 1}  # passes the caller's edge type + hops straight through
    ev = answer.evidence[0]
    assert ev.entity_id == "0000000002" and ev.name == "Beta"
    assert ev.chunk_ids == ["docA:0:h"]  # cited (no claim without a citation, FR-Q.6)
    assert ev.path_entity_ids == ["0000000001", "0000000002"]  # entity_id evidence chain
    assert ev.confidences == ["EXTRACTED"]  # confidence surfaced


def test_confidence_is_surfaced_not_filtered():
    # an AMBIGUOUS edge is still returned as evidence (T26 surfaces, does not gate — FR-C.5/FR-Q.3)
    rows = [_row("x", "X", 1, ["a", "x"], ["c1"], ["AMBIGUOUS"])]
    answer = graph_query("a", store=_FakeStore(rows), relationship_type="related_to")
    assert answer.evidence[0].confidences == ["AMBIGUOUS"]  # present, not dropped


def test_two_hop_evidence_carries_the_full_path():
    rows = [_row("0000000003", "Gamma", 2, ["0000000001", "0000000002", "0000000003"],
                 ["docA:0:h", "docB:1:h"], ["EXTRACTED", "INFERRED"])]
    answer = graph_query("0000000001", store=_FakeStore(rows), relationship_type="related_to", max_hops=2)
    ev = answer.evidence[0]
    assert ev.hops == 2
    assert ev.path_entity_ids == ["0000000001", "0000000002", "0000000003"]
    assert ev.chunk_ids == ["docA:0:h", "docB:1:h"]  # both edges' chunks cited


# (EP-CORE-1a/ADR-0118: graph_query is de-registered from ARD — a core primitive now; registration test removed.)


def test_graph_query_is_domain_neutral_no_contract_imports():
    """graph_query is a GENERIC engine primitive: it must not import the contract ontology (no CONTRACTS_WITH
    default, no RelationshipType). A domain caller names the edge-type string (ADR-0117 / DD-5)."""
    import inspect

    from rag_wright.capabilities import graph_query as mod

    src = inspect.getsource(mod)
    assert "rag_wright.contracts" not in src and "RelationshipType" not in src


# --- live ArcadeDB (opt-in): real traversal ------------------------------------------------------

_DB = "ragwright_graph_query_test"


@pytest.fixture
def live_graph():
    from rag_wright.store.arcadedb import ArcadeDBStore
    from rag_wright.store.seam import GraphEdge, GraphNode

    store = ArcadeDBStore.from_env(database=_DB, reset=True)
    store.ensure_schema()
    # A contracts with B and C (one-hop); B contracts with D (two-hop from A)
    nodes = [GraphNode(node_key=k, entity_id=k, name=n, entity_type="Organization",
                       confidence="EXTRACTED", chunk_id="docA:0:h")
             for k, n in [("A", "Acme"), ("B", "Beta"), ("C", "Gamma"), ("D", "Delta")]]
    cw = "Contracts With"  # DD-5: the edge type is an opaque domain string
    edges = [
        GraphEdge(source_key="A", target_key="B", relationship_type=cw, confidence="EXTRACTED", chunk_id="c1"),
        GraphEdge(source_key="A", target_key="C", relationship_type=cw, confidence="EXTRACTED", chunk_id="c2"),
        GraphEdge(source_key="B", target_key="D", relationship_type=cw, confidence="EXTRACTED", chunk_id="c3"),
    ]
    store.write_graph(nodes, edges)
    yield store
    store.drop()
    store.close()


@pytest.mark.store
def test_live_one_hop_returns_cited_co_parties(live_graph):
    answer = graph_query("A", store=live_graph, relationship_type="Contracts With", max_hops=1)
    targets = {e.entity_id: e for e in answer.evidence}
    assert set(targets) == {"B", "C"}  # A's direct co-parties
    assert targets["B"].chunk_ids == ["c1"]  # cited from the edge's chunk
    assert all(e.confidences == ["EXTRACTED"] for e in answer.evidence)


@pytest.mark.store
def test_live_two_hop_reaches_the_far_entity_with_path_evidence(live_graph):
    answer = graph_query("A", store=live_graph, relationship_type="Contracts With", max_hops=2)
    two_hop = [e for e in answer.evidence if e.hops == 2]
    assert any(e.entity_id == "D" for e in two_hop)  # A -> B -> D reachable
    d = next(e for e in two_hop if e.entity_id == "D")
    assert d.path_entity_ids == ["A", "B", "D"]  # the traversal chain
    assert d.chunk_ids == ["c1", "c3"]  # both edges on the path cited
