"""EP-REF-1a-i (ADR-0117): the generic `kg_edges` edge-traversal primitive on the store.

One hermetic test proves scope-to-nothing short-circuits with no query. The `-m store` tests run against a live
ArcadeDB and prove the three idioms behind the one surface over a real (contract) clause KG seeded through
`ContractKGStore`: out-traversal (exact key and contract-scope key-range + edge/target where), in-traversal, and
the direct edge scan (span-id on the edge -> `inV().value`). The primitive itself is domain-free -- the clause KG
is just a convenient real graph to traverse.
"""
from __future__ import annotations

import pytest

from rag_wright.capabilities.contract_kg_store import ContractKGStore
from rag_wright.contracts.identifiers import ChunkId
from rag_wright.contracts.property import ClausePropertyRecord, PropertyAssertion, PropertyDimension
from rag_wright.contracts.provenance import ConfidenceTag, Provenance
from rag_wright.store.arcadedb import CLAUSE_TYPE, PROPVALUE_TYPE, ArcadeDBStore
from rag_wright.store.seam import NOT_NULL

_D = PropertyDimension
_X = ConfidenceTag.EXTRACTED
_TEST_DB = "ragwright_test_kg_edges"


def _clause(contract: str, idx: int, function: str, props) -> tuple[ClausePropertyRecord, str, str]:
    """A one-clause record whose clause_id is contract-prefixed (`<contract>:<idx>:<hash>`), so the contract-scope
    key-range covers it. Returns (record, clause_id, span_id)."""
    cid = ChunkId.of(contract, idx, f"{contract} body {idx}")
    span_id = f"{cid}#0"
    prov = Provenance.of(cid)
    assertions = [
        PropertyAssertion(provenance=prov, confidence=_X, dimension=d, value=v, span_id=span_id)
        for d, v in props
    ]
    return ClausePropertyRecord(clause_id=str(cid), function=function, assertions=assertions), str(cid), span_id


# --- hermetic: scope-to-nothing short-circuits (no query issued) --------------------------------


def test_empty_membership_returns_empty_without_querying():
    store = object.__new__(ArcadeDBStore)  # no DB; prove kg_edges never reaches _query

    def _boom(sql):  # pragma: no cover - must not be called
        raise AssertionError(f"kg_edges issued a query for an empty membership: {sql}")

    store._query = _boom  # type: ignore[attr-defined]
    # empty membership in the edge scan, the start where, and the target where each scope to nothing
    assert store.kg_edges(edge_type="HAS_MUTUALITY", edge_where={"span_id": []}, select={"v": "inV().value"}) == []
    assert store.kg_edges(CLAUSE_TYPE, where={"clause_id": []}, select={"c": "c.clause_id"}) == []
    assert store.kg_edges(CLAUSE_TYPE, where={"clause_id": "x"}, target_where={"value": []},
                          select={"c": "c.clause_id"}) == []


# --- live ArcadeDB (opt-in) ---------------------------------------------------------------------


@pytest.fixture
def store():
    s = ArcadeDBStore.from_env(database=_TEST_DB, reset=True)
    s.ensure_schema()
    yield s
    s.drop()
    s.close()


@pytest.mark.store
def test_out_traversal_from_exact_key(store):
    rec, cid, _ = _clause("K", 0, "Cap On Liability", [(_D.MUTUALITY, "mutual"), (_D.CARVE_OUT, "fraud")])
    ContractKGStore(store).write_clause_kg(rec)

    rows = store.kg_edges(
        CLAUSE_TYPE, where={"clause_id": cid}, direction="out",
        select={"edge_type": "e.@type", "dimension": "e.dimension", "value": "v.value"})

    by_dim = {r["dimension"]: r for r in rows}
    assert by_dim["mutuality"]["edge_type"] == "HAS_MUTUALITY"
    assert by_dim["mutuality"]["value"] == "mutual"
    assert by_dim["carve_out"]["value"] == "fraud"


@pytest.mark.store
def test_out_traversal_key_range_with_edge_and_target_where(store):
    # two clauses in ONE contract "C"; only clause 0 asserts mutuality=mutual
    kg = ContractKGStore(store)
    rec0, cid0, _ = _clause("C", 0, "Cap On Liability", [(_D.MUTUALITY, "mutual")])
    rec1, cid1, _ = _clause("C", 1, "Cap On Liability", [(_D.MUTUALITY, "unilateral")])
    kg.write_clause_kg(rec0)
    kg.write_clause_kg(rec1)

    rows = store.kg_edges(
        CLAUSE_TYPE, key_range=("clause_id", "C:", "C;"), direction="out",
        edge_where={"dimension": "mutuality"}, target_where={"value": "mutual"},
        select={"clause_id": "c.clause_id"})

    assert [r["clause_id"] for r in rows] == [cid0]  # the range + edge/target where select only clause 0


@pytest.mark.store
def test_in_traversal_reaches_the_source_clause(store):
    rec, cid, _ = _clause("K", 0, "Cap On Liability", [(_D.MUTUALITY, "mutual")])
    ContractKGStore(store).write_clause_kg(rec)

    # from the shared PropertyValue node (value=mutual), follow the incoming HAS_MUTUALITY edge to its Clause
    rows = store.kg_edges(
        PROPVALUE_TYPE, where={"value": "mutual"}, direction="in", edge_type="HAS_MUTUALITY",
        select={"clause_id": "v.clause_id"})

    assert [r["clause_id"] for r in rows] == [cid]


@pytest.mark.store
def test_edge_scan_projects_target_value_by_span_id(store):
    rec, _, span_id = _clause("K", 0, "Cap On Liability", [(_D.MUTUALITY, "mutual")])
    ContractKGStore(store).write_clause_kg(rec)

    # the span_properties idiom: scan the edge table by the edge's span_id, project the target value
    rows = store.kg_edges(
        edge_type="HAS_MUTUALITY", edge_where={"span_id": [span_id]},
        select={"span_id": "span_id", "value": "inV().value"})

    assert rows == [{"span_id": span_id, "value": "mutual"}]
    assert store.kg_edges(edge_type="HAS_MUTUALITY", edge_where={"span_id": []},
                          select={"span_id": "span_id"}) == []  # scope-to-nothing
