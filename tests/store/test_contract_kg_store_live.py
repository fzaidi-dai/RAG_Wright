"""PS-8a (G21): every `ContractKGStore` method that used raw store SQL, exercised end to end on a live ArcadeDB, so
the move onto the generic primitives (`kg_read` / `kg_count` / `kg_delete` / `kg_update` / `kg_write` / `kg_edges`)
is proven behaviour-identical. Run on the old code first (parity baseline), then on the rewritten store.

It also reproduces a live bug: `write_clause_exception_links` called `self._db`, which `ContractKGStore` never had
(left over from ING-8e moving the method off `ArcadeDBStore`), so the `clause_exception_linking` capability raised
`AttributeError` as soon as it had links to write."""
from __future__ import annotations

import pytest

from rag_wright.contracts.chunk import BGE_M3_DENSE_DIM
from rag_wright.contracts.identifiers import ChunkId
from rag_wright.contracts.provenance import ConfidenceTag, Provenance
from rag_wright.contracts.span import SpanRecord
from rag_wright.packs.contracts.capabilities.clause_exception_linking import (
    CAP_FUNCTION,
    EXCEPTION_FUNCTION,
    clause_exception_linking,
)
from rag_wright.packs.contracts.capabilities.contract_kg_store import ContractKGStore
from rag_wright.packs.contracts.schemas.contract_meta import ContractRecord
from rag_wright.packs.contracts.schemas.property import ClausePropertyRecord, PropertyAssertion, PropertyDimension
from rag_wright.store.arcadedb import ArcadeDBStore

pytestmark = pytest.mark.store

_D = PropertyDimension


@pytest.fixture
def kg():
    s = ArcadeDBStore.from_env(database="ragwright_test_ckg_primitives", reset=True)
    s.ensure_schema()
    yield ContractKGStore(s)
    s.drop()
    s.close()


def _clause(kg, contract: str, idx: int, function: str, start: int, props=()) -> tuple[str, str]:
    """Write one clause (with its operative span at document offset `start`) and return (clause_id, span_id)."""
    text = f"{contract} clause {idx} {function}"
    cid = ChunkId.of(contract, idx, text)
    span_id = f"{cid}#0"
    kg._store.upsert_span(SpanRecord(
        span_id=span_id, parent_chunk_id=str(cid), span_index=0, text=text, primary_tag=function, tags=[function],
        dense_vector=[0.01] * BGE_M3_DENSE_DIM, sparse_vector={1: 0.5}, document_id=contract,
        doc_start=start, doc_end=start + 100))
    assertions = [PropertyAssertion(provenance=Provenance.of(cid), confidence=ConfidenceTag.EXTRACTED, dimension=d,
                                    value=v, span_id=span_id) for d, v in props]
    kg.write_clause_kg(ClausePropertyRecord(clause_id=str(cid), function=function, span_id=span_id,
                                            assertions=assertions))
    return str(cid), span_id


def _seed(kg):
    for c in ("C", "D"):
        kg.upsert_contract(ContractRecord(contract_id=c, name=f"Contract {c}"))
    cap, cap_span = _clause(kg, "C", 0, CAP_FUNCTION, 100, [(_D.MUTUALITY, "mutual"), (_D.CARVE_OUT, "fraud")])
    unc, _ = _clause(kg, "C", 1, EXCEPTION_FUNCTION, 400)
    law, _ = _clause(kg, "C", 2, "Governing Law", 9000)
    d_cap, _ = _clause(kg, "D", 0, CAP_FUNCTION, 50, [(_D.MUTUALITY, "unilateral")])
    return {"cap": cap, "cap_span": cap_span, "unc": unc, "law": law, "d_cap": d_cap}


def test_reads_all_contracts_clauses_in_contract_and_positions(kg):
    ids = _seed(kg)
    assert sorted(r["contract_id"] for r in kg.all_contracts()) == ["C", "D"]
    assert [r["clause_id"] for r in kg.clauses_in_contract("C")] == sorted([ids["cap"], ids["unc"], ids["law"]])
    pos = {r["clause_id"]: r for r in kg.clause_positions([CAP_FUNCTION, EXCEPTION_FUNCTION])}
    assert set(pos) == {ids["cap"], ids["unc"], ids["d_cap"]}
    assert (pos[ids["unc"]]["contract_id"], pos[ids["unc"]]["doc_start"], pos[ids["unc"]]["doc_end"]) == ("C", 400, 500)
    assert kg.clause_positions([]) == []


def test_exception_linking_writes_the_link_and_is_idempotent(kg):
    ids = _seed(kg)
    for _ in range(2):  # re-linking clears the layer first, so it never duplicates
        result = clause_exception_linking(kg)
        assert [(lk.exception_clause_id, lk.cap_clause_id) for lk in result.links] == [(ids["unc"], ids["cap"])]
    rows = kg.exceptions_of_clause(ids["cap"])
    assert [r["clause_id"] for r in rows] == [ids["unc"]]
    assert kg._store.kg_count("IsExceptionTo") == 1


def test_counts_mark_stale_and_clear_the_typed_clause_kg(kg):
    ids = _seed(kg)
    assert kg.clause_kg_counts() == {"clauses": 4, "property_values": 3, "typed_edges": 3}
    assert kg.mark_span_properties_ambiguous([ids["cap_span"]]) == 2
    assert kg.mark_span_properties_ambiguous([ids["cap_span"]]) == 0  # already AMBIGUOUS: nothing changes
    assert kg.mark_span_properties_ambiguous([]) == 0
    assert {e["confidence"] for e in kg.clause_typed_edges(ids["cap"])} == {"AMBIGUOUS"}
    assert {e["confidence"] for e in kg.clause_typed_edges(ids["d_cap"])} == {"EXTRACTED"}
    kg.clear_clause_kg()
    assert kg.clause_kg_counts() == {"clauses": 0, "property_values": 0, "typed_edges": 0}
    assert kg._store.kg_count("Span") == 4  # the span index is left intact


def test_the_legacy_flat_property_graph_counts_values_and_clear(kg):
    cid = ChunkId.of("E", 0, "E clause 0")
    span_id = f"{cid}#0"
    kg.write_property_graph(ClausePropertyRecord(
        clause_id=str(cid), function=CAP_FUNCTION, span_id=span_id,
        assertions=[PropertyAssertion(provenance=Provenance.of(cid), confidence=ConfidenceTag.EXTRACTED,
                                      dimension=_D.MUTUALITY, value="mutual", span_id=span_id)]))
    assert kg.property_graph_counts() == {"clauses": 1, "property_values": 1, "property_edges": 1}
    assert kg.clause_property_values(str(cid)) == [
        {"dimension": "mutuality", "value": "mutual", "confidence": "EXTRACTED", "span_id": span_id}]
    kg.clear_property_graph()
    assert kg.property_graph_counts() == {"clauses": 0, "property_values": 0, "property_edges": 0}
