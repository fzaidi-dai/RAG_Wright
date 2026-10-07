"""CU-B3 (ADR-0029): Contract vertex + within-contract typed span filter (live ArcadeDB, -m store)."""

from __future__ import annotations

import json

import pytest

from rag_wright.packs.contracts.capabilities.contract_kg_store import ContractKGStore
from rag_wright.contracts.chunk import BGE_M3_DENSE_DIM
from rag_wright.packs.contracts.schemas.contract_meta import ContractRecord
from rag_wright.contracts.span import SpanRecord
from rag_wright.store.arcadedb import ArcadeDBStore

_TEST_DB = "ragwright_test_contract"


def _dense() -> list[float]:
    v = [0.0] * BGE_M3_DENSE_DIM
    v[0] = 1.0
    return v


def _span(cid: str, span_id: str, function: str, doc_start: int) -> SpanRecord:
    return SpanRecord(span_id=span_id, parent_chunk_id=f"{cid}:0:h", span_index=0, text=f"span {span_id}",
                      function=function, dense_vector=_dense(), sparse_vector={1: 1.0},
                      contract_id=cid, doc_start=doc_start, doc_end=doc_start + 10)


@pytest.fixture
def store():
    s = ArcadeDBStore.from_env(database=_TEST_DB, reset=True)
    s.ensure_schema()
    yield s
    s.drop()
    s.close()


@pytest.mark.store
def test_contract_schema_created(store):
    assert "Contract" in store.type_names()
    assert "Contract[contract_id]" in store.index_names()
    assert {"contract_id", "name", "agreement_type", "parties_json", "page_count"} <= store.property_names("Contract")


@pytest.mark.store
def test_upsert_and_lookup_contract(store):
    ContractKGStore(store).upsert_contract(ContractRecord(contract_id="C1", name="Distributor Agreement",
                                         agreement_type="Distribution", parties=["Acme", "Beta"], page_count=12))
    row = store.contract_by_id("C1")
    assert row["contract_id"] == "C1" and row["name"] == "Distributor Agreement"
    assert json.loads(row["parties_json"]) == ["Acme", "Beta"]
    assert row["page_count"] == 12
    assert store.contract_by_id("nope") is None  # absent -> None


@pytest.mark.store
def test_spans_by_contract_typed_filter(store):
    store.upsert_span(_span("C1", "C1:0:h#0", "Governing Law", 100))
    store.upsert_span(_span("C1", "C1:0:h#1", "Governing Law", 10))
    store.upsert_span(_span("C1", "C1:0:h#2", "Indemnification", 50))
    store.upsert_span(_span("C2", "C2:0:h#0", "Governing Law", 5))  # another contract -- must not leak in

    gl = store.spans_by_contract("C1", ["Governing Law"])
    assert [r["span_id"] for r in gl] == ["C1:0:h#1", "C1:0:h#0"]  # C1 only, ordered by doc_start (10, 100)
    assert all(r["contract_id"] == "C1" for r in gl)

    both = store.spans_by_contract("C1", ["Governing Law", "Indemnification"])  # multi-type
    assert {r["span_id"] for r in both} == {"C1:0:h#0", "C1:0:h#1", "C1:0:h#2"}

    assert store.spans_by_contract("C1", ["Non-Compete"]) == []  # absent type -> not present
    assert store.spans_by_contract("C1", []) == []  # empty function set -> empty
