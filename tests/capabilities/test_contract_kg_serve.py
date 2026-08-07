"""KG-4 (FR-Q, ADR-0033): Leg A intra-contract scoped-query serving over the typed KG.

Hermetic tests drive the assembly/scoping logic with a fake store; the `-m store` test writes a synthetic
two-clause contract into a live ArcadeDB and proves real disambiguation (the mutual cap vs the unilateral one).
"""

from __future__ import annotations

import pytest

from rag_wright.capabilities.contract_kg_serve import (
    aggregate_by_property,
    clauses_of_function,
    contract_clause_index,
    disambiguate,
    grounded_only,
)
from rag_wright.contracts.identifiers import ChunkId
from rag_wright.contracts.property import ClausePropertyRecord, PropertyAssertion, PropertyDimension
from rag_wright.contracts.provenance import ConfidenceTag, Provenance

_CID = "ACME_2020_MSA"
_A = f"{_CID}:0:aaa"  # a mutual cap clause
_B = f"{_CID}:1:bbb"  # a unilateral cap clause


class _FakeStore:
    """Canned readbacks matching the three KG-query store methods."""

    def clauses_in_contract(self, contract_id):
        return [
            {"clause_id": _A, "function": "Cap On Liability", "folio_iri": "folio:CAP"},
            {"clause_id": _B, "function": "Cap On Liability", "folio_iri": "folio:CAP"},
            {"clause_id": f"{_CID}:2:ccc", "function": "Governing Law", "folio_iri": ""},  # property-less
        ]

    def contract_clause_kg(self, contract_id):
        return [
            {"clause_id": _A, "function": "Cap On Liability", "edge_type": "HAS_MUTUALITY",
             "dimension": "mutuality", "value": "mutual", "predicate_iri": "cbr:HAS_MUTUALITY",
             "confidence": "EXTRACTED", "span_id": "sA"},
            {"clause_id": _A, "function": "Cap On Liability", "edge_type": "EXCEPTS",
             "dimension": "carve_out", "value": "fraud", "predicate_iri": "cbr:EXCEPTS",
             "confidence": "AMBIGUOUS", "span_id": "sA"},
            {"clause_id": _B, "function": "Cap On Liability", "edge_type": "HAS_MUTUALITY",
             "dimension": "mutuality", "value": "unilateral", "predicate_iri": "cbr:HAS_MUTUALITY",
             "confidence": "EXTRACTED", "span_id": "sB"},
        ]

    def clauses_with_property(self, contract_id, dimension, value):
        m = {
            ("mutuality", "mutual"): [{"clause_id": _A, "function": "Cap On Liability"}],
            ("mutuality", "unilateral"): [{"clause_id": _B, "function": "Cap On Liability"}],
            ("carve_out", "fraud"): [{"clause_id": _A, "function": "Cap On Liability"}],
        }
        return m.get((dimension, value), [])


def test_contract_clause_index_assembles_every_clause_with_properties() -> None:
    idx = contract_clause_index(_FakeStore(), _CID)
    assert [c.clause_id for c in idx] == [_A, _B, f"{_CID}:2:ccc"]
    a = next(c for c in idx if c.clause_id == _A)
    assert a.contract_id == _CID and a.function == "Cap On Liability"
    assert {(p.dimension, p.value) for p in a.properties} == {("mutuality", "mutual"), ("carve_out", "fraud")}
    # a property-less clause still appears (queryable by type), with no properties
    gl = next(c for c in idx if c.function == "Governing Law")
    assert gl.properties == []


def test_contract_clause_index_skips_non_property_edges_no_crash() -> None:
    """ADR-0044 regression: an IsExceptionTo (clause->clause) edge carries NO dimension/value. The
    clause_kg outE traversal can surface such a row; serving must skip it (not a typed property), never
    crash on a null dimension nor attach a bogus empty property (the clean-fixtures-hide-real-data lesson)."""

    class _StoreWithExceptionEdge(_FakeStore):
        def contract_clause_kg(self, contract_id):
            return super().contract_clause_kg(contract_id) + [
                {"clause_id": _A, "function": "Cap On Liability", "edge_type": "IsExceptionTo",
                 "dimension": None, "value": None, "predicate_iri": None,
                 "confidence": "INFERRED", "span_id": None},
            ]

    idx = contract_clause_index(_StoreWithExceptionEdge(), _CID)
    a = next(c for c in idx if c.clause_id == _A)
    # the two REAL properties survive; the non-property IsExceptionTo row is dropped (no empty property)
    assert {(p.dimension, p.value) for p in a.properties} == {("mutuality", "mutual"), ("carve_out", "fraud")}
    assert all(p.dimension for p in a.properties)


def test_disambiguate_same_type_clauses_by_property() -> None:
    """Two Cap clauses; disambiguate returns only the mutual one (what classification can't do)."""
    got = disambiguate(_FakeStore(), _CID, "Cap On Liability", "mutuality", "mutual")
    assert [c.clause_id for c in got] == [_A]
    got_uni = disambiguate(_FakeStore(), _CID, "Cap On Liability", "mutuality", "unilateral")
    assert [c.clause_id for c in got_uni] == [_B]


def test_clauses_of_function_aggregates_by_type() -> None:
    caps = clauses_of_function(_FakeStore(), _CID, "Cap On Liability")
    assert {c.clause_id for c in caps} == {_A, _B}


def test_aggregate_by_property_across_types() -> None:
    got = aggregate_by_property(_FakeStore(), _CID, "carve_out", "fraud")
    assert [c.clause_id for c in got] == [_A]


def test_answers_are_cited() -> None:
    a = next(c for c in contract_clause_index(_FakeStore(), _CID) if c.clause_id == _A)
    for p in a.properties:
        assert p.span_id and p.confidence  # every property carries provenance (FR-Q.6)


def test_grounded_only_drops_ambiguous() -> None:
    idx = contract_clause_index(_FakeStore(), _CID)
    a = next(c for c in grounded_only(idx) if c.clause_id == _A)
    assert {(p.dimension, p.value) for p in a.properties} == {("mutuality", "mutual")}  # fraud (AMBIGUOUS) dropped


# --- live ArcadeDB (opt-in) ---------------------------------------------------------------------

_TEST_DB = "ragwright_test_lega"


def _cap(contract: str, idx: int, mutuality: str):
    cid = ChunkId.of(contract, idx, f"cap clause {idx}")
    prov = Provenance.of(cid)
    rec = ClausePropertyRecord(
        clause_id=str(cid), function="Cap On Liability",
        assertions=[PropertyAssertion(
            provenance=prov, confidence=ConfidenceTag.EXTRACTED,
            dimension=PropertyDimension.MUTUALITY, value=mutuality, span_id=f"{cid}#0",
        )],
    )
    return rec


@pytest.fixture
def store():
    from rag_wright.store.arcadedb import ArcadeDBStore

    s = ArcadeDBStore.from_env(database=_TEST_DB, reset=True)
    s.ensure_schema()
    yield s
    s.drop()
    s.close()


@pytest.mark.store
def test_live_disambiguate_over_typed_kg(store) -> None:
    store.write_clause_kg(_cap("ACME_MSA", 0, "mutual"))
    store.write_clause_kg(_cap("ACME_MSA", 1, "unilateral"))
    store.write_clause_kg(_cap("OTHER_CO", 0, "mutual"))  # different contract -- must not leak in

    idx = contract_clause_index(store, "ACME_MSA")
    assert len(idx) == 2  # scoped to the contract; OTHER_CO excluded

    got = disambiguate(store, "ACME_MSA", "Cap On Liability", "mutuality", "mutual")
    assert len(got) == 1 and got[0].clause_id.startswith("ACME_MSA:0:")
    assert got[0].properties[0].dimension == "mutuality" and got[0].properties[0].value == "mutual"
