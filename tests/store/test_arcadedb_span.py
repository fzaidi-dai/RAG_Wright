"""T55 (FR-R, ADR-0025): the ArcadeDB `Span` operative-span hybrid index.

Hermetic test proves the `SpanRecord` contract; the `-m store` tests run against a live ArcadeDB (the same
scratch-database pattern as the chunk schema tests) and prove the real behaviour: the dense `LSM_VECTOR` +
sparse `LSM_SPARSE_VECTOR` span indexes, upsert + RRF-fused retrieval, the parent pointer round-tripping, and
the `function` filter that the classifier routes with.
"""

from __future__ import annotations

import pytest

from rag_wright.contracts.chunk import BGE_M3_DENSE_DIM
from rag_wright.contracts.span import SpanRecord
from rag_wright.store.arcadedb import ArcadeDBStore

_TEST_DB = "ragwright_test_span"


def _dense(i: int) -> list[float]:
    v = [0.0] * BGE_M3_DENSE_DIM
    v[i] = 1.0
    return v


# --- hermetic contract --------------------------------------------------------------------------


def test_span_record_enforces_dense_dimension():
    with pytest.raises(ValueError):
        SpanRecord(span_id="a#0", parent_chunk_id="a", span_index=0, text="x",
                   dense_vector=[0.1, 0.2], sparse_vector={1: 1.0})  # wrong length
    ok = SpanRecord(span_id="a#0", parent_chunk_id="a", span_index=0, text="x",
                    dense_vector=_dense(0), sparse_vector={1: 1.0})
    assert ok.function == "" and ok.span_id == "a#0"


# --- live ArcadeDB (opt-in) ---------------------------------------------------------------------


@pytest.fixture
def store():
    s = ArcadeDBStore.from_env(database=_TEST_DB, reset=True)
    s.ensure_schema()
    yield s
    s.drop()
    s.close()


@pytest.mark.store
def test_span_schema_and_indexes_created(store):
    assert "Span" in store.type_names()
    idx = store.index_names()
    assert {"Span[dense]", "Span[sparse_indices,sparse_weights]", "Span[span_id]"} <= idx
    assert {"span_id", "parent_chunk_id", "parent_okf_path", "function", "dense",
            "sparse_indices", "sparse_weights",
            "contract_id", "doc_start", "doc_end"} <= store.property_names("Span")  # CU-B2


@pytest.mark.store
def test_upsert_and_hybrid_search_returns_parent_pointer(store):
    a = SpanRecord(span_id="aaa:0:h#0", parent_chunk_id="aaa:0:h",
                   parent_okf_path="limitation-of-liability/aaa.md", span_index=0,
                   text="In no event shall Supplier be liable for consequential damages.",
                   function="Cap On Liability", dense_vector=_dense(0), sparse_vector={1: 1.0, 2: 0.5})
    b = SpanRecord(span_id="bbb:0:h#0", parent_chunk_id="bbb:0:h",
                   parent_okf_path="indemnification/bbb.md", span_index=0,
                   text="Supplier shall indemnify and hold harmless Buyer.",
                   function="Indemnification", dense_vector=_dense(5), sparse_vector={7: 1.0})
    store.upsert_span(a)
    store.upsert_span(b)

    hits = store.span_hybrid_search(_dense(0), {1: 1.0, 2: 0.5}, k=5)
    assert hits and hits[0]["span_id"] == "aaa:0:h#0"  # nearest to A's dense+sparse
    assert hits[0]["parent_chunk_id"] == "aaa:0:h"  # parent pointer round-trips
    assert hits[0]["parent_okf_path"] == "limitation-of-liability/aaa.md"


@pytest.mark.store
def test_function_filter_restricts_despite_identical_vectors(store):
    # identical vectors, different function -> the filter alone must decide
    a = SpanRecord(span_id="a#0", parent_chunk_id="a", span_index=0, text="cap",
                   function="Cap On Liability", dense_vector=_dense(0), sparse_vector={1: 1.0})
    b = SpanRecord(span_id="b#0", parent_chunk_id="b", span_index=0, text="indemnity",
                   function="Indemnification", dense_vector=_dense(0), sparse_vector={1: 1.0})
    store.upsert_span(a)
    store.upsert_span(b)

    hits = store.span_hybrid_search(_dense(0), {1: 1.0}, k=5, function="Cap On Liability")
    assert {h["span_id"] for h in hits} == {"a#0"}  # b excluded despite identical vectors


@pytest.mark.store
def test_cuad_offset_columns_persist_and_round_trip(store):
    # CU-B2: the doc-absolute citation offsets + contract_id persist and slice the canonical text back.
    canonical = "PREAMBLE.\n\n(a) No consequential damages. (b) Cap: fees paid.\n\nEXHIBIT"
    text = "(a) No consequential damages. "
    start = canonical.index(text)
    rec = SpanRecord(span_id="cu1:0:h#0", parent_chunk_id="cu1:0:h", span_index=0, text=text,
                     function="Cap On Liability", dense_vector=_dense(3), sparse_vector={4: 1.0},
                     contract_id="cu1", doc_start=start, doc_end=start + len(text))
    store.upsert_span(rec)

    row = store._query("SELECT contract_id, doc_start, doc_end, text FROM Span WHERE span_id = 'cu1:0:h#0'")[0]
    assert row["contract_id"] == "cu1"
    assert (row["doc_start"], row["doc_end"]) == (start, start + len(text))
    assert canonical[row["doc_start"] : row["doc_end"]] == row["text"] == text  # citation invariant persists
