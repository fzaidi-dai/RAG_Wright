"""T57c (FR-C.6/FR-C.7, ADR-0025/0026): the ArcadeDB property graph.

Hermetic test proves the value-node key; the `-m store` tests run against a live ArcadeDB (the scratch-
database pattern) and prove the real behaviour: the Clause / PropertyValue / HasProperty schema, the write
with provenance on every edge, the SHARED value node deduped across clauses (what the graph buys over a
flat filter), and idempotent re-population.
"""

from __future__ import annotations

import pytest

from rag_wright.contracts.chunk import BGE_M3_DENSE_DIM
from rag_wright.contracts.identifiers import ChunkId
from rag_wright.contracts.property import ClausePropertyRecord, PropertyAssertion, PropertyDimension
from rag_wright.contracts.provenance import ConfidenceTag, Provenance
from rag_wright.contracts.span import SpanRecord
from rag_wright.store.arcadedb import ArcadeDBStore, _property_value_key

_TEST_DB = "ragwright_test_property"


def _record(seed: str, function: str, props) -> tuple[ClausePropertyRecord, str]:
    cid = ChunkId.of(seed, 0, seed + " body")
    prov = Provenance.of(cid)
    assertions = [
        PropertyAssertion(provenance=prov, confidence=c, dimension=d, value=v, span_id=f"{cid}#0")
        for d, v, c in props
    ]
    return ClausePropertyRecord(clause_id=str(cid), function=function, assertions=assertions), str(cid)


# --- hermetic -----------------------------------------------------------------------------------


def test_property_value_key_is_dimension_and_value():
    assert _property_value_key("carve_out", "indemnification") == "carve_out:indemnification"


# --- live ArcadeDB (opt-in) ---------------------------------------------------------------------


@pytest.fixture
def store():
    s = ArcadeDBStore.from_env(database=_TEST_DB, reset=True)
    s.ensure_schema()
    yield s
    s.drop()
    s.close()


@pytest.mark.store
def test_property_schema_and_indexes_created(store):
    assert {"Clause", "PropertyValue", "HasProperty"} <= store.type_names()
    assert {"Clause[clause_id]", "PropertyValue[value_key]"} <= store.index_names()
    assert {"clause_id", "function", "folio_iri"} <= store.property_names("Clause")


@pytest.mark.store
def test_write_and_readback_with_provenance(store):
    rec, cid = _record("capA", "Cap On Liability", [
        (PropertyDimension.MUTUALITY, "mutual", ConfidenceTag.EXTRACTED),
        (PropertyDimension.CARVE_OUT, "indemnification", ConfidenceTag.EXTRACTED),
    ])
    store.write_property_graph(rec)
    assert store.property_graph_counts() == {"clauses": 1, "property_values": 2, "property_edges": 2}
    got = store.clause_property_values(cid)
    assert {(r["dimension"], r["value"]) for r in got} == {
        ("mutuality", "mutual"), ("carve_out", "indemnification"),
    }
    for r in got:  # provenance surfaced on every edge (FR-S.4 / FR-Q.6)
        assert r["confidence"] == "EXTRACTED" and r["span_id"] == f"{cid}#0"


@pytest.mark.store
def test_shared_value_node_deduped_across_clauses(store):
    # two different clauses that both carve out indemnification must share ONE value node -- this is what
    # lets a property query traverse (:PropertyValue{carve_out:indemnification})<-(:Clause) instead of scanning
    a, _ = _record("capA", "Cap On Liability", [(PropertyDimension.CARVE_OUT, "indemnification", ConfidenceTag.EXTRACTED)])
    b, _ = _record("capB", "Cap On Liability", [(PropertyDimension.CARVE_OUT, "indemnification", ConfidenceTag.EXTRACTED)])
    store.write_property_graph(a)
    store.write_property_graph(b)
    counts = store.property_graph_counts()
    assert counts == {"clauses": 2, "property_values": 1, "property_edges": 2}  # ONE shared value node


@pytest.mark.store
def test_clear_property_graph_keeps_spans(store):
    store.upsert_span(SpanRecord(span_id="s#0", parent_chunk_id="s", span_index=0, text="x",
                                 dense_vector=[0.0] * BGE_M3_DENSE_DIM, sparse_vector={1: 1.0}))
    rec, _ = _record("capA", "Cap On Liability", [(PropertyDimension.MUTUALITY, "mutual", ConfidenceTag.EXTRACTED)])
    store.write_property_graph(rec)
    assert store.property_graph_counts()["clauses"] == 1
    store.clear_property_graph()
    assert store.property_graph_counts() == {"clauses": 0, "property_values": 0, "property_edges": 0}
    # the span index is untouched (re-extraction does not re-embed)
    assert store._query("SELECT count(*) AS n FROM Span")[0]["n"] == 1


@pytest.mark.store
def test_repopulation_is_idempotent(store):
    rec, _ = _record("capA", "Cap On Liability", [(PropertyDimension.MUTUALITY, "mutual", ConfidenceTag.EXTRACTED)])
    store.write_property_graph(rec)
    store.write_property_graph(rec)  # a re-extract of the same clause must not duplicate the edge
    counts = store.property_graph_counts()
    assert counts["clauses"] == 1 and counts["property_edges"] == 1
