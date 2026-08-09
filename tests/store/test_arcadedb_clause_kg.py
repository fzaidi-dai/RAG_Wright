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
from rag_wright.store.arcadedb import (
    TYPED_PROPERTY_EDGE_TYPES,
    ArcadeDBStore,
    _clause_kg_statements,
    _edge_predicate_iri,
    _TYPED_DIMENSION_EDGE,
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


def test_every_dimension_maps_to_a_typed_edge() -> None:
    """A new PropertyDimension cannot silently break the write path: the map must be total."""
    assert set(_TYPED_DIMENSION_EDGE) == set(PropertyDimension)


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
    }


def test_deontic_edges_ground_to_odrl_others_to_bridge() -> None:
    assert _edge_predicate_iri("PROHIBITS") == "http://www.w3.org/ns/odrl/2/prohibition"
    assert _edge_predicate_iri("REQUIRES") == "http://www.w3.org/ns/odrl/2/obligation"
    assert _edge_predicate_iri("HAS_MUTUALITY") == (
        "https://ragwright.local/ontology/contract-bridge#HAS_MUTUALITY"
    )


def test_statements_use_typed_edges_with_predicate_and_provenance() -> None:
    rec, _cid = _record("capA", "Cap On Liability", [
        (_D.MUTUALITY, "mutual", ConfidenceTag.EXTRACTED),
        (_D.CARVE_OUT, "fraud", ConfidenceTag.EXTRACTED),
        (_D.DAMAGE_TYPE, "consequential", ConfidenceTag.EXTRACTED),
    ])
    statements = _clause_kg_statements(rec)
    sql = "\n".join(statements)
    # typed edges, not the legacy flat one
    assert "CREATE EDGE HAS_MUTUALITY" in sql
    assert "CREATE EDGE EXCEPTS" in sql
    assert "CREATE EDGE PROHIBITS" in sql  # damage_type waiver
    assert "CREATE EDGE HasProperty" not in sql
    # predicate IRI grounding (ODRL on the deontic edge) + provenance on every edge
    assert "http://www.w3.org/ns/odrl/2/prohibition" in sql
    assert sql.count("predicate_iri = ") == 3
    assert sql.count("confidence = ") == 3
    # span_id on all 3 property edges AND the Clause vertex itself (clause-level span_id; persist-clause-span-id)
    assert sql.count("span_id = ") == 4
    assert statements[0].startswith("UPDATE Clause SET") and "span_id = " in statements[0]


def test_value_nodes_get_folio_grounding() -> None:
    """A carve-out subject with a FOLIO concept IRI grounds its shared value node."""
    rec, _cid = _record("x", "Cap On Liability", [(_D.CARVE_OUT, "fraud", ConfidenceTag.EXTRACTED)])
    sql = "\n".join(_clause_kg_statements(rec))
    assert "https://folio.openlegalstandard.org/RqGxSnAp9vX42GRKHqwvBe" in sql  # fraud FOLIO IRI


def test_cuad_extension_dims_route_to_grants_and_has_edges() -> None:
    """KG-4: exclusivity -> GRANTS (ODRL permission IRI); an open dim -> its HAS_* edge."""
    rec, _cid = _record("excl", "Exclusivity", [
        (_D.EXCLUSIVITY_TYPE, "exclusive", ConfidenceTag.EXTRACTED),
        (_D.AUDIT_FREQUENCY, "annual", ConfidenceTag.EXTRACTED),
    ])
    sql = "\n".join(_clause_kg_statements(rec))
    assert "CREATE EDGE GRANTS" in sql
    assert "http://www.w3.org/ns/odrl/2/permission" in sql  # GRANTS grounds to ODRL permission
    assert "CREATE EDGE HAS_AUDIT_FREQUENCY" in sql


def test_empty_record_writes_only_the_clause_node() -> None:
    rec, _cid = _record("empty", "Cap On Liability", [])
    stmts = _clause_kg_statements(rec)
    assert len(stmts) == 1 and stmts[0].startswith("UPDATE Clause")


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
    store.write_clause_kg(rec)
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
    store.write_clause_kg(rec)
    store.write_clause_kg(rec)  # content-hash gate -> no duplicate edges
    assert store.clause_kg_counts() == {"clauses": 1, "property_values": 1, "typed_edges": 1}


@pytest.mark.store
def test_shared_value_node_deduped_across_clauses(store) -> None:
    """The KG win: two clauses asserting the same (dimension,value) share one value node."""
    for seed in ("c1", "c2"):
        rec, _ = _record(seed, "Cap On Liability", [(_D.CARVE_OUT, "fraud", ConfidenceTag.EXTRACTED)])
        store.write_clause_kg(rec)
    counts = store.clause_kg_counts()
    assert counts["clauses"] == 2 and counts["property_values"] == 1 and counts["typed_edges"] == 2


@pytest.mark.store
def test_clear_clause_kg_empties_the_typed_graph(store) -> None:
    rec, _cid = _record("capC", "Cap On Liability", [(_D.CARVE_OUT, "fraud", ConfidenceTag.EXTRACTED)])
    store.write_clause_kg(rec)
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


def test_clause_upsert_emits_functions_json(): 
    # ADR-0048: the Clause UPSERT persists the multi-label classification as a JSON string (primary-first).
    import json

    from rag_wright.contracts.function import FunctionConfidence, FunctionScore

    rec, _ = _record("s1", "Cap On Liability", [])
    rec = rec.model_copy(update={"functions": [
        FunctionScore(function="Cap On Liability", confidence=FunctionConfidence.HIGH),
        FunctionScore(function="Indemnification", confidence=FunctionConfidence.MEDIUM)]})
    clause_update = _clause_kg_statements(rec)[0]
    assert "functions = " in clause_update
    payload = json.dumps([{"function": "Cap On Liability", "confidence": "high"},
                          {"function": "Indemnification", "confidence": "medium"}])
    assert payload in clause_update  # exact JSON payload present in the SET clause


def test_clause_upsert_empty_functions_is_empty_json_array():
    rec, _ = _record("s2", "Governing Law", [])
    assert '[]' in _clause_kg_statements(rec)[0]
