"""issue 0031 / ADR-0094: a workspace `documents` scope on corpus retrieval, applied IN THE STORE.

`span_hybrid_search` filters `contract_id IN [...]`; `graph_neighbors` filters `source_doc_id IN [...]` on
EVERY edge of a path; `write_graph`/`add_affiliation_edges` stamp `source_doc_id` on each Relationship edge
(derived from its provenance chunk_id); `known_document_ids` is the union used for unknown-document
validation. Hermetic -- `_query`/`_db` stubbed, no live DB.
"""

from __future__ import annotations

from types import SimpleNamespace

from rag_wright.store.arcadedb import ArcadeDBStore, _doc_id_of
from rag_wright.store.seam import GraphEdge, GraphNode


def _capturing_store(rows_for=lambda sql: []):
    calls: list[str] = []
    s = object.__new__(ArcadeDBStore)

    def _query(sql: str):
        calls.append(sql)
        return rows_for(sql)

    s._query = _query
    return s, calls


# --- span_hybrid_search ---------------------------------------------------------------------------------------

def test_span_hybrid_search_scopes_to_documents():
    store, calls = _capturing_store()
    store.span_hybrid_search([0.1, 0.2], {1: 0.5}, k=8, documents=["docA", "docB"])
    sql = calls[-1]
    assert "contract_id IN ['docA','docB']" in sql  # the store-side workspace cut
    assert " 1000)" in sql or "1000" in sql  # SCOPED_CANDIDATE_POOL: a larger KNN pool when scoped


def test_span_hybrid_search_combines_function_and_documents():
    store, calls = _capturing_store()
    store.span_hybrid_search([0.1], {1: 0.5}, k=8, function="indemnification", documents=["docA"])
    sql = calls[-1]
    assert "function = 'indemnification'" in sql and "contract_id IN ['docA']" in sql and " AND " in sql


def test_span_hybrid_search_empty_documents_is_scope_to_nothing():
    store, calls = _capturing_store()
    assert store.span_hybrid_search([0.1], {1: 0.5}, k=8, documents=[]) == []
    assert calls == []  # never issues an invalid `IN []`


def test_span_hybrid_search_none_documents_unchanged():
    store, calls = _capturing_store()
    store.span_hybrid_search([0.1], {1: 0.5}, k=8, documents=None)
    assert "contract_id IN" not in calls[-1]  # whole index, no doc filter


# --- graph_neighbors ------------------------------------------------------------------------------------------

def test_graph_neighbors_scopes_every_edge_to_documents():
    store, calls = _capturing_store()
    store.graph_neighbors("e1", relationship_type="Contracts With", max_hops=2, documents=["docA", "docB"])
    one_hop, two_hop = calls[0], calls[1]
    assert one_hop.count("source_doc_id IN ['docA','docB']") == 1  # the single edge
    assert two_hop.count("source_doc_id IN ['docA','docB']") == 2  # BOTH hops pruned (no routing through)


def test_graph_neighbors_empty_documents_is_scope_to_nothing():
    store, calls = _capturing_store()
    assert store.graph_neighbors("e1", relationship_type="Contracts With", max_hops=1, documents=[]) == []
    assert calls == []


def test_graph_neighbors_none_documents_unchanged():
    store, calls = _capturing_store()
    store.graph_neighbors("e1", relationship_type="Contracts With", max_hops=1, documents=None)
    assert "source_doc_id IN" not in calls[0]


# --- edge source_doc_id stamping ------------------------------------------------------------------------------

def test_doc_id_of_takes_prefix_before_first_colon():
    assert _doc_id_of("docA:0:deadbeef") == "docA"
    assert _doc_id_of("my_contract-2020:12:abc") == "my_contract-2020"
    assert _doc_id_of("") == ""


def test_write_graph_stamps_source_doc_id_on_relationship_edge():
    tx: list[str] = []
    store = object.__new__(ArcadeDBStore)
    store._query = lambda sql: [{"c": 0}] if "count(*)" in sql else []  # nothing exists yet
    store._db = SimpleNamespace(execute_transaction=lambda stmts: tx.extend(stmts))
    nodes = [GraphNode(node_key="UNLINKED:a", entity_id="", name="A", entity_type="Organization",
                       confidence="EXTRACTED", chunk_id="docA:0:hash"),
             GraphNode(node_key="UNLINKED:b", entity_id="", name="B", entity_type="Organization",
                       confidence="EXTRACTED", chunk_id="docA:0:hash")]
    edges = [GraphEdge(source_key="UNLINKED:a", target_key="UNLINKED:b", relationship_type="Contracts With",
                       confidence="EXTRACTED", chunk_id="docA:0:hash")]
    store.write_graph(nodes, edges)
    rel = [s for s in tx if s.startswith("CREATE EDGE Relationship")]
    assert len(rel) == 1 and "source_doc_id = 'docA'" in rel[0]  # derived from the provenance chunk_id


def test_edge_chunk_id_and_source_doc_id_are_a_consistent_pair():
    # issue 0031 ask #2: chunk_id + source_doc_id are written from ONE source, so they can never drift
    # (a drifted pair is an invisible cross-matter leak). A tricky doc prefix (dots/dashes) stays consistent.
    from rag_wright.store.arcadedb import _doc_id_of, _edge_provenance_assignments

    frag = _edge_provenance_assignments("weird.doc-1:2:abcdef")
    assert "chunk_id = 'weird.doc-1:2:abcdef'" in frag
    assert f"source_doc_id = '{_doc_id_of('weird.doc-1:2:abcdef')}'" in frag  # derived, not independent
    assert "source_doc_id = 'weird.doc-1'" in frag


def test_add_affiliation_edges_stamps_source_doc_id():
    commands: list[str] = []
    store = object.__new__(ArcadeDBStore)
    store._query = lambda sql: [{"c": 0}] if "count(*)" in sql else []
    store._command = lambda sql: commands.append(sql)
    nodes = [GraphNode(node_key="UNLINKED:a", entity_id="", name="A", entity_type="Organization",
                       confidence="EXTRACTED", chunk_id="docZ:3:hh"),
             GraphNode(node_key="UNLINKED:b", entity_id="", name="B", entity_type="Organization",
                       confidence="EXTRACTED", chunk_id="docZ:3:hh")]
    edges = [GraphEdge(source_key="UNLINKED:a", target_key="UNLINKED:b", relationship_type="Affiliate Of",
                       confidence="EXTRACTED", chunk_id="docZ:3:hh")]
    assert store.add_affiliation_edges(nodes, edges) == 1
    rel = [c for c in commands if c.startswith("CREATE EDGE Relationship")]
    assert len(rel) == 1 and "source_doc_id = 'docZ'" in rel[0]


# --- known_document_ids ---------------------------------------------------------------------------------------

def test_known_document_ids_unions_spans_and_edges():
    def rows(sql: str):
        if "FROM Span" in sql:
            return [{"d": "docA"}, {"d": "docB"}, {"d": None}]
        if "FROM Relationship" in sql:
            return [{"d": "docB"}, {"d": "docC"}]
        return []

    store, _ = _capturing_store(rows)
    store.type_names = lambda: {"Span", "Relationship"}
    assert store.known_document_ids() == {"docA", "docB", "docC"}  # union, None dropped


def test_known_document_ids_missing_types_empty():
    store, _ = _capturing_store()
    store.type_names = lambda: set()
    assert store.known_document_ids() == set()
