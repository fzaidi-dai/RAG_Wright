"""KG-3 (FR-C/FR-S, ADR-0033): the TYPED unified clause KG write path.

Hermetic tests prove the pure statement builder and the dimension->edge mapping (no store); the `-m store`
tests run against a live ArcadeDB and prove the real behaviour: the typed edge schema, a typed edge per
assertion with its predicate IRI + provenance, the shared value node preserved, idempotent re-population,
and the clear.
"""

from __future__ import annotations

import pytest

from rag_wright.contracts.identifiers import ChunkId
from rag_wright.contracts.property import ClausePropertyRecord, PropertyAssertion, PropertyDimension
from rag_wright.contracts.provenance import ConfidenceTag, Provenance
from rag_wright.capabilities.contract_kg_store import ContractKGStore
from rag_wright.store.arcadedb import (
    TYPED_PROPERTY_EDGE_TYPES,
    ArcadeDBStore,
    _edge_predicate_iri,
    _stale_property_statements,
)

_D = PropertyDimension
_TEST_DB = "ragwright_test_clause_kg"


def _record(seed: str, function: str, props) -> tuple[ClausePropertyRecord, str]:
    cid = ChunkId.of(seed, 0, seed + " body")
    prov = Provenance.of(cid)
    assertions = [
        PropertyAssertion(provenance=prov, confidence=c, dimension=d, value=v, span_id=f"{cid}#0")
        for d, v, c in props
    ]
    return ClausePropertyRecord(clause_id=str(cid), function=function, assertions=assertions), str(cid)


# --- hermetic -----------------------------------------------------------------------------------


def test_sanctioned_edge_set() -> None:
    """The distinct edge types are exactly the ADR-0033 sanctioned property edges."""
    assert set(TYPED_PROPERTY_EDGE_TYPES) == {
        # ACORD-derived (KG-3)
        "HAS_MUTUALITY", "HAS_FAVORABILITY", "HAS_ASYMMETRY", "HAS_WARRANTY_SCOPE", "HAS_CLAIM_SCOPE",
        "HAS_IP_OWNERSHIP", "HAS_RENEWAL", "EXCEPTS", "COVERS", "PROHIBITS", "REQUIRES", "CAPS",
        "BOUNDED_BY", "GOVERNED_BY",
        # CUAD-family extensions (KG-4): deontic -> GRANTS/PROHIBITS; the rest -> HAS_*
        "GRANTS", "HAS_COC_CONSENT", "HAS_ASSIGNMENT_CONSENT", "HAS_ESCROW_TRIGGER", "HAS_MFN_SCOPE",
        "HAS_TERMINATION_RIGHT", "HAS_AUDIT_FREQUENCY", "HAS_COMMITMENT_QUANTUM", "HAS_LD_TRIGGER",
        # ADR-0049 (2): dispute_method -> HAS_DISPUTE_METHOD; collateral_type -> SECURES; force-majeure + royalty
        # get new HAS_* edges; confidentiality exceptions reuse EXCEPTS; conditions reuse REQUIRES.
        "HAS_DISPUTE_METHOD", "SECURES", "HAS_FORCE_MAJEURE_EVENT", "HAS_ROYALTY_BASIS",
    }


def test_deontic_edges_ground_to_odrl_others_to_bridge() -> None:
    assert _edge_predicate_iri("PROHIBITS") == "http://www.w3.org/ns/odrl/2/prohibition"
    assert _edge_predicate_iri("REQUIRES") == "http://www.w3.org/ns/odrl/2/obligation"
    assert _edge_predicate_iri("HAS_MUTUALITY") == (
        "https://ragwright.local/ontology/contract-bridge#HAS_MUTUALITY"
    )


# --- live ArcadeDB (opt-in) ---------------------------------------------------------------------


@pytest.fixture
def store():
    s = ArcadeDBStore.from_env(database=_TEST_DB, reset=True)
    s.ensure_schema()
    yield s
    s.drop()
    s.close()


@pytest.mark.store
def test_typed_edge_schema_created(store) -> None:
    assert set(TYPED_PROPERTY_EDGE_TYPES) <= store.type_names()


@pytest.mark.store
def test_write_typed_kg_and_readback(store) -> None:
    rec, cid = _record("capA", "Cap On Liability", [
        (_D.MUTUALITY, "mutual", ConfidenceTag.EXTRACTED),
        (_D.CARVE_OUT, "fraud", ConfidenceTag.EXTRACTED),
        (_D.DAMAGE_TYPE, "consequential", ConfidenceTag.EXTRACTED),
        (_D.CAP_QUANTUM, "12_months", ConfidenceTag.EXTRACTED),
    ])
    ContractKGStore(store).write_clause_kg(rec)
    assert store.clause_kg_counts() == {"clauses": 1, "property_values": 4, "typed_edges": 4}

    got = store.clause_typed_edges(cid)
    by_dim = {r["dimension"]: r for r in got}
    assert by_dim["mutuality"]["edge_type"] == "HAS_MUTUALITY"
    assert by_dim["carve_out"]["edge_type"] == "EXCEPTS"
    assert by_dim["damage_type"]["edge_type"] == "PROHIBITS"
    assert by_dim["damage_type"]["predicate_iri"] == "http://www.w3.org/ns/odrl/2/prohibition"
    assert by_dim["carve_out"]["folio_iri"] == "https://folio.openlegalstandard.org/RqGxSnAp9vX42GRKHqwvBe"
    for r in got:  # provenance on every typed edge (FR-S.4 / FR-Q.6)
        assert r["confidence"] == "EXTRACTED" and r["span_id"] == f"{cid}#0"


@pytest.mark.store
def test_write_is_idempotent(store) -> None:
    rec, _cid = _record("capB", "Cap On Liability", [(_D.MUTUALITY, "mutual", ConfidenceTag.EXTRACTED)])
    ContractKGStore(store).write_clause_kg(rec)
    ContractKGStore(store).write_clause_kg(rec)  # content-hash gate -> no duplicate edges
    assert store.clause_kg_counts() == {"clauses": 1, "property_values": 1, "typed_edges": 1}


@pytest.mark.store
def test_shared_value_node_deduped_across_clauses(store) -> None:
    """The KG win: two clauses asserting the same (dimension,value) share one value node."""
    for seed in ("c1", "c2"):
        rec, _ = _record(seed, "Cap On Liability", [(_D.CARVE_OUT, "fraud", ConfidenceTag.EXTRACTED)])
        ContractKGStore(store).write_clause_kg(rec)
    counts = store.clause_kg_counts()
    assert counts["clauses"] == 2 and counts["property_values"] == 1 and counts["typed_edges"] == 2


@pytest.mark.store
def test_clear_clause_kg_empties_the_typed_graph(store) -> None:
    rec, _cid = _record("capC", "Cap On Liability", [(_D.CARVE_OUT, "fraud", ConfidenceTag.EXTRACTED)])
    ContractKGStore(store).write_clause_kg(rec)
    store.clear_clause_kg()
    assert store.clause_kg_counts() == {"clauses": 0, "property_values": 0, "typed_edges": 0}


def test_span_properties_joins_typed_edges_by_span_id():
    """SPAN-CLAUSE-RERANK: `span_properties` aggregates (dimension, value) per span across the typed edge
    types via the `edge.span_id == Span.span_id` join. Hermetic -- a fake `_query` returns canned edge rows."""
    store = ArcadeDBStore.__new__(ArcadeDBStore)
    rows = {
        "HAS_MUTUALITY": [{"span_id": "s1", "dimension": "mutuality", "value": "mutual"}],
        "CAPS": [{"span_id": "s1", "dimension": "cap_basis", "value": "fixed_fee"},
                 {"span_id": "s2", "dimension": "cap_quantum", "value": None}],  # None value -> skipped
        "GOVERNED_BY": [{"span_id": "sX", "dimension": "jurisdiction", "value": "england"}],  # span not in batch
    }

    def fake_query(sql):
        for edge_type, r in rows.items():
            if f"FROM {edge_type} " in sql:
                return r
        return []

    store._query = fake_query
    out = store.span_properties(["s1", "s2"])
    assert out["s1"] == {("mutuality", "mutual"), ("cap_basis", "fixed_fee")}  # aggregated across edge types
    assert out["s2"] == set()  # its only row had value=None -> nothing recorded
    assert "sX" not in out  # rows for spans outside the requested batch are ignored
    assert store.span_properties([]) == {}  # empty in -> empty out, no query


# --- ADR-0048 Phase A mark-stale: the pure UPDATE builder (no store) --------------------------------------


def test_stale_property_statements_one_update_per_edge_type_scoped_to_spans():
    stmts = _stale_property_statements(["s1", "s2"])
    # one UPDATE per typed property edge type, no more (bounded by edge-type count, not pool size)
    assert len(stmts) == len(TYPED_PROPERTY_EDGE_TYPES)
    joined = "\n".join(stmts)
    for edge_type in TYPED_PROPERTY_EDGE_TYPES:
        assert f"UPDATE {edge_type} SET confidence = 'AMBIGUOUS'" in joined
    # scoped to the given spans by the ADR-0025 span_id key, and idempotent (skips already-AMBIGUOUS)
    for s in stmts:
        assert "WHERE span_id IN ['s1','s2']" in s
        assert "confidence <> 'AMBIGUOUS'" in s


def test_stale_property_statements_empty_is_noop():
    assert _stale_property_statements([]) == []
