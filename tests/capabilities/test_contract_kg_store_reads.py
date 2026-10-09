"""EP-REF-1b-ii (R3b, ADR-0117): the contract traversal + vocab reference reads on `ContractKGStore` (what
EP-SEAM-3 lifts). Hermetic tests prove the DD-5 edge-name routing (counterparties=CONTRACTS_WITH,
affiliates=AFFILIATE_OF) and the full (not grounded-only) term view; the `-m store` test proves the two
traversals separate on a live entity graph and that the clause terms read back.
"""
from __future__ import annotations

import pytest

from rag_wright.packs.contracts.capabilities.contract_kg_store import ContractKGStore


# --- hermetic: party_counterparties / party_affiliates route the right DD-5 edge name -----------


def test_party_traversals_name_the_right_edge_and_thread_scope(monkeypatch):
    import rag_wright.capabilities.graph_query as gq
    from rag_wright.capabilities.graph_query import GraphAnswer

    calls = []

    def _fake(start_entity_id, *, store, relationship_type, max_hops=1, documents=None):
        calls.append({"start": start_entity_id, "rel": relationship_type, "hops": max_hops, "docs": documents})
        return GraphAnswer(start_entity_id=start_entity_id, relationship_type=relationship_type, evidence=[])

    monkeypatch.setattr(gq, "graph_query", _fake)
    ckg = ContractKGStore(object())  # graph_query is faked; the store is never touched

    ckg.party_counterparties("acme", max_hops=2, documents=["d1"])
    ckg.party_affiliates("acme", documents=["d1"])

    assert calls[0] == {"start": "acme", "rel": "Contracts With", "hops": 2, "docs": ["d1"]}  # DD-5 co-party edge
    assert calls[1] == {"start": "acme", "rel": "Affiliate Of", "hops": 1, "docs": ["d1"]}  # separate traversal


# --- hermetic: contract_terms is the FULL view (keeps AMBIGUOUS props, not grounded-only) -------


class _FakeStore:
    """Fakes the GENERIC store surface ContractKGStore delegates to: kg_edges (contract_clause_kg's edge scan)
    + kg_read (a contract's clauses). So contract_terms exercises the REAL reshaping (contract_clause_index over kg_edges)."""

    _A = "C:0:aaa"

    def kg_edges(self, from_type=None, *, where=None, key_range=None, direction="out", edge_type=None,
                 edge_where=None, target_where=None, select):
        return [
            {"clause_id": self._A, "function": "Cap On Liability", "edge_type": "HAS_MUTUALITY",
             "dimension": "mutuality", "value": "mutual", "predicate_iri": "cbr:HAS_MUTUALITY",
             "folio_iri": "", "confidence": "EXTRACTED", "span_id": "sA"},
            {"clause_id": self._A, "function": "Cap On Liability", "edge_type": "EXCEPTS",
             "dimension": "carve_out", "value": "the modification of the Software",  # ADR-0102 verbatim out-of-vocab
             "predicate_iri": "cbr:EXCEPTS", "folio_iri": "", "confidence": "AMBIGUOUS", "span_id": "sA"},
        ]

    def kg_read(self, node_type, *, key_range=None, **kw):  # a contract's clauses: a clause-id range read (PS-8a)
        assert node_type == "Clause" and key_range == ("clause_id", "C:", "C;")
        return [{"clause_id": self._A, "function": "Cap On Liability", "folio_iri": "folio:CAP"}]


def test_contract_terms_keeps_ambiguous_properties():
    terms = ContractKGStore(_FakeStore()).contract_terms("C")
    assert len(terms) == 1
    props = {(p.dimension, p.value) for p in terms[0].properties}
    # BOTH props survive -- the AMBIGUOUS carve-out (a verbatim out-of-vocab value) is NOT dropped
    assert props == {("mutuality", "mutual"), ("carve_out", "the modification of the Software")}


# --- live ArcadeDB (opt-in) ----------------------------------------------------------------------


@pytest.fixture
def store():
    from rag_wright.store.arcadedb import ArcadeDBStore

    s = ArcadeDBStore.from_env(database="ragwright_test_ckg_reads", reset=True)
    s.ensure_schema()
    yield s
    s.drop()
    s.close()


@pytest.mark.store
def test_counterparties_and_affiliates_separate_on_a_live_graph(store):
    from rag_wright.store.seam import GraphEdge, GraphNode

    nodes = [GraphNode(node_key=k, entity_id=k, name=n, entity_type="Organization",
                       confidence="EXTRACTED", chunk_id="d:0:h")
             for k, n in [("A", "Acme"), ("B", "Beta"), ("E", "Acme Holdings")]]
    edges = [
        GraphEdge(source_key="A", target_key="B", relationship_type="Contracts With", confidence="EXTRACTED", chunk_id="c1"),
        GraphEdge(source_key="A", target_key="E", relationship_type="Affiliate Of", confidence="EXTRACTED", chunk_id="c2"),
    ]
    store.write_graph(nodes, edges)
    ckg = ContractKGStore(store)

    cps = {e.entity_id for e in ckg.party_counterparties("A")}
    affs = {e.entity_id for e in ckg.party_affiliates("A")}
    assert cps == {"B"}  # only the CONTRACTS_WITH edge -- not the affiliate
    assert affs == {"E"}  # only the AFFILIATE_OF edge -- not the counterparty


@pytest.mark.store
def test_contract_terms_reads_the_clause_kg_live(store):
    from rag_wright.contracts.identifiers import ChunkId
    from rag_wright.packs.contracts.schemas.property import ClausePropertyRecord, PropertyAssertion, PropertyDimension
    from rag_wright.contracts.provenance import ConfidenceTag, Provenance

    cid = ChunkId.of("K", 0, "K body")
    rec = ClausePropertyRecord(
        clause_id=str(cid), function="Cap On Liability",
        assertions=[PropertyAssertion(provenance=Provenance.of(cid), confidence=ConfidenceTag.EXTRACTED,
                                      dimension=PropertyDimension.MUTUALITY, value="mutual", span_id=f"{cid}#0")])
    ckg = ContractKGStore(store)
    ckg.write_clause_kg(rec)

    terms = ckg.contract_terms("K")
    assert len(terms) == 1 and terms[0].clause_id == str(cid)
    assert ("mutuality", "mutual") in {(p.dimension, p.value) for p in terms[0].properties}


def test_contract_kg_store_delegates_all_spans_by_contract():
    """Regression (intra_document_qa abstained for EVERY document): `contract_clause_index(..., include_untyped=True)`
    calls `store.all_spans_by_contract`, but the serve store is a ContractKGStore. It must answer it (over the generic
    `all_spans_by_document`, ING-8e), else serve raised AttributeError -> (caught) -> empty clauses -> abstain."""
    from rag_wright.packs.contracts.capabilities.contract_kg_store import ContractKGStore

    class _Raw:
        def all_spans_by_document(self, contract_id):
            return [{"span_id": f"{contract_id}:0:h#0", "function": "", "text": "a clause body"}]

    ckg = ContractKGStore(_Raw())
    assert ckg.all_spans_by_contract("C1") == [{"span_id": "C1:0:h#0", "function": "", "text": "a clause body"}]
