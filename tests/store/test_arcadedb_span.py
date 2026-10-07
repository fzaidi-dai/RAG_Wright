"""T55 (FR-R, ADR-0025): the ArcadeDB `Span` operative-span hybrid index.

Hermetic test proves the `SpanRecord` contract; the `-m store` tests run against a live ArcadeDB (the same
scratch-database pattern as the chunk schema tests) and prove the real behaviour: the dense `LSM_VECTOR` +
sparse `LSM_SPARSE_VECTOR` span indexes, upsert + RRF-fused retrieval, the parent pointer round-tripping, and
the `primary_tag` filter that the classifier routes with (ING-8d: the engine span fields are `document_id`,
`primary_tag`, `tags`; the legacy `parent_okf_path` locator is gone).
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
    assert ok.primary_tag == "" and ok.tags == [] and ok.document_id == "" and ok.span_id == "a#0"
    assert not {"function", "functions", "contract_id", "parent_okf_path"} & set(SpanRecord.model_fields)  # ING-8d


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
    props = store.property_names("Span")
    assert {"span_id", "parent_chunk_id", "primary_tag", "tags", "dense", "sparse_indices", "sparse_weights",
            "document_id", "doc_start", "doc_end"} <= props  # CU-B2, ING-8d names
    assert not {"function", "functions", "contract_id", "parent_okf_path"} & props  # ING-8d: no old names


@pytest.mark.store
def test_upsert_and_hybrid_search_returns_parent_pointer(store):
    a = SpanRecord(span_id="aaa:0:h#0", parent_chunk_id="aaa:0:h", span_index=0,
                   text="In no event shall Supplier be liable for consequential damages.",
                   primary_tag="Cap On Liability", dense_vector=_dense(0), sparse_vector={1: 1.0, 2: 0.5})
    b = SpanRecord(span_id="bbb:0:h#0", parent_chunk_id="bbb:0:h", span_index=0,
                   text="Supplier shall indemnify and hold harmless Buyer.",
                   primary_tag="Indemnification", dense_vector=_dense(5), sparse_vector={7: 1.0})
    store.upsert_span(a)
    store.upsert_span(b)

    hits = store.span_hybrid_search(_dense(0), {1: 1.0, 2: 0.5}, k=5)
    assert hits and hits[0]["span_id"] == "aaa:0:h#0"  # nearest to A's dense+sparse
    assert hits[0]["parent_chunk_id"] == "aaa:0:h"  # parent pointer round-trips
    assert hits[0]["primary_tag"] == "Cap On Liability"  # ING-8d: the row carries the new field name


@pytest.mark.store
def test_primary_tag_filter_restricts_despite_identical_vectors(store):
    # identical vectors, different primary tag -> the filter alone must decide
    a = SpanRecord(span_id="a#0", parent_chunk_id="a", span_index=0, text="cap",
                   primary_tag="Cap On Liability", dense_vector=_dense(0), sparse_vector={1: 1.0})
    b = SpanRecord(span_id="b#0", parent_chunk_id="b", span_index=0, text="indemnity",
                   primary_tag="Indemnification", dense_vector=_dense(0), sparse_vector={1: 1.0})
    store.upsert_span(a)
    store.upsert_span(b)

    hits = store.span_hybrid_search(_dense(0), {1: 1.0}, k=5, primary_tag="Cap On Liability")
    assert {h["span_id"] for h in hits} == {"a#0"}  # b excluded despite identical vectors


@pytest.mark.store
def test_cuad_offset_columns_persist_and_round_trip(store):
    # CU-B2: the doc-absolute citation offsets + document_id persist and slice the canonical text back.
    canonical = "PREAMBLE.\n\n(a) No consequential damages. (b) Cap: fees paid.\n\nEXHIBIT"
    text = "(a) No consequential damages. "
    start = canonical.index(text)
    rec = SpanRecord(span_id="cu1:0:h#0", parent_chunk_id="cu1:0:h", span_index=0, text=text,
                     primary_tag="Cap On Liability", tags=["Cap On Liability", "Uncapped Liability"],
                     dense_vector=_dense(3), sparse_vector={4: 1.0},
                     document_id="cu1", doc_start=start, doc_end=start + len(text))
    store.upsert_span(rec)

    row = store._query("SELECT document_id, tags, doc_start, doc_end, text FROM Span WHERE span_id = 'cu1:0:h#0'")[0]
    assert row["document_id"] == "cu1"
    assert row["tags"] == '["Cap On Liability", "Uncapped Liability"]'  # JSON list, primary-first
    assert (row["doc_start"], row["doc_end"]) == (start, start + len(text))
    assert canonical[row["doc_start"] : row["doc_end"]] == row["text"] == text  # citation invariant persists


# --- ING-8d: old-schema guard + migration ----------------------------------------------------------------


def _old_schema_span(store) -> None:
    """A Span type as a pre-ING-8d engine created it, with one record carrying the old field names."""
    store._command("DROP TYPE Span UNSAFE")
    store._command("CREATE VERTEX TYPE Span")
    for prop, kind in (("span_id", "STRING"), ("parent_chunk_id", "STRING"), ("parent_okf_path", "STRING"),
                       ("span_index", "INTEGER"), ("text", "STRING"), ("function", "STRING"), ("functions", "STRING"),
                       ("dense", "ARRAY_OF_FLOATS"), ("sparse_indices", "ARRAY_OF_INTEGERS"),
                       ("sparse_weights", "ARRAY_OF_FLOATS"), ("contract_id", "STRING"), ("doc_start", "INTEGER"),
                       ("doc_end", "INTEGER"), ("pages", "ARRAY_OF_INTEGERS"), ("bbox", "STRING")):
        store._command(f"CREATE PROPERTY Span.{prop} {kind}")
    store._command("INSERT INTO Span SET span_id = 'old#0', contract_id = 'docX', function = 'Cap On Liability',"
                   " functions = '[\"Cap On Liability\"]', parent_okf_path = 'acord/1'")


@pytest.mark.store
def test_ensure_schema_refuses_an_unmigrated_span_type(store):
    _old_schema_span(store)
    with pytest.raises(RuntimeError, match="migrate_span_fields"):
        store.ensure_schema()  # fail loud: an old KG must never be read with silently-empty filters


@pytest.mark.store
def test_migrate_span_fields_moves_values_and_is_idempotent(store):
    _old_schema_span(store)
    assert store.migrate_span_fields() == 1
    assert store.migrate_span_fields() == 0  # idempotent: nothing left to move
    row = store._query("SELECT FROM Span WHERE span_id = 'old#0'")[0]
    assert (row["document_id"], row["primary_tag"], row["tags"]) == ("docX", "Cap On Liability", '["Cap On Liability"]')
    assert not {"contract_id", "function", "functions"} & set(row)
    assert row["parent_okf_path"] == "acord/1"  # a legacy locator is left in place (no longer engine-declared)
    store.ensure_schema()  # migrated -> accepted
    assert not {"contract_id", "function", "functions"} & store.property_names("Span")
