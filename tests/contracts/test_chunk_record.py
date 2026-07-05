"""Tests for the chunk record contract (T3, FR-I.3 / FR-S.1, RAC-3).

The chunk record is the one record per chunk in the hybrid retrieval index (FR-S.1). It holds the
chunk_id, the summary, the dense-over-summary vector, the sparse-over-full-text vector, keywords,
entities, and source metadata (FR-I.3). The load-bearing part of the contract is the vector shapes:
they must match what BGE-M3 produces (T19) and what the ArcadeDB LSM_VECTOR / LSM_SPARSE_VECTOR
indexes bind (T13), so the tests pin dimension, key/value ranges, and JSON round-tripping.
"""

import pytest
from pydantic import ValidationError

from rag_wright.contracts.chunk import BGE_M3_DENSE_DIM, ChunkRecord
from rag_wright.contracts.identifiers import ChunkId


def _dense(fill=0.1):
    return [fill] * BGE_M3_DENSE_DIM


def _record(**overrides):
    base = dict(
        chunk_id=ChunkId.of("doc-1", 0, "full chunk text"),
        summary="A short summary of the chunk.",
        dense_vector=_dense(),
        sparse_vector={6: 0.42, 10001: 0.13},
        keywords=["termination", "governing law"],
        entity_mentions=["Acme Corp", "Beta LLC"],
        source_metadata={"doc_type": "contract", "pages": 12},
    )
    base.update(overrides)
    return ChunkRecord(**base)


def test_bge_m3_dense_dim_is_declared():
    assert BGE_M3_DENSE_DIM == 1024


def test_valid_chunk_record_carries_all_fields():
    rec = _record()
    assert rec.chunk_id.source_doc_id == "doc-1"
    assert rec.summary
    assert len(rec.dense_vector) == BGE_M3_DENSE_DIM
    assert rec.sparse_vector == {6: 0.42, 10001: 0.13}
    assert rec.keywords == ["termination", "governing law"]
    assert rec.entity_mentions == ["Acme Corp", "Beta LLC"]
    assert rec.source_metadata["doc_type"] == "contract"


def test_chunk_id_is_required():
    with pytest.raises(ValidationError):
        _record(chunk_id=None)


# --- dense vector: fixed BGE-M3 dimension, finite (RAC-3) ------------------------------------


@pytest.mark.parametrize("n", [0, 1, BGE_M3_DENSE_DIM - 1, BGE_M3_DENSE_DIM + 1])
def test_dense_vector_must_have_bge_m3_dimension(n):
    with pytest.raises(ValidationError):
        _record(dense_vector=[0.1] * n)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_dense_vector_rejects_non_finite(bad):
    v = _dense()
    v[0] = bad
    with pytest.raises(ValidationError):
        _record(dense_vector=v)


# --- sparse vector: store-bindable token-id -> weight map (RAC-3) ----------------------------


def test_sparse_vector_accepts_bge_m3_string_keys_and_coerces_to_int():
    # BGE-M3 lexical_weights come out as Dict[str, float]; the store binds int indices.
    rec = _record(sparse_vector={"6": 0.42, "10001": 0.13})
    assert rec.sparse_vector == {6: 0.42, 10001: 0.13}
    assert all(isinstance(k, int) for k in rec.sparse_vector)


def test_sparse_vector_rejects_negative_token_id():
    with pytest.raises(ValidationError):
        _record(sparse_vector={-1: 0.5})


@pytest.mark.parametrize("bad_key", ["abc", "1.5", ""])
def test_sparse_vector_rejects_non_integer_keys(bad_key):
    # Non-integer-convertible keys are rejected loudly, never silently dropped.
    with pytest.raises(ValidationError):
        _record(sparse_vector={bad_key: 0.5})


@pytest.mark.parametrize("bad", [-0.1, float("nan"), float("inf")])
def test_sparse_vector_rejects_bad_weights(bad):
    with pytest.raises(ValidationError):
        _record(sparse_vector={6: bad})


def test_sparse_vector_may_be_empty():
    assert _record(sparse_vector={}).sparse_vector == {}


# --- text fields and metadata (RAC-3) -------------------------------------------------------


@pytest.mark.parametrize("bad_summary", ["", "   "])
def test_summary_must_be_non_empty(bad_summary):
    with pytest.raises(ValidationError):
        _record(summary=bad_summary)


def test_keywords_and_entity_mentions_default_empty():
    rec = _record(keywords=[], entity_mentions=[])
    assert rec.keywords == [] and rec.entity_mentions == []


@pytest.mark.parametrize("field", ["keywords", "entity_mentions"])
def test_keywords_and_entity_mentions_reject_empty_strings(field):
    with pytest.raises(ValidationError):
        _record(**{field: ["ok", "  "]})


def test_source_metadata_rejects_non_scalar_values():
    # metadata must stay filterable JSON scalars (FR-Q.1); a nested list is not.
    with pytest.raises(ValidationError):
        _record(source_metadata={"parties": ["Acme", "Beta"]})


def test_source_metadata_defaults_empty():
    assert _record(source_metadata={}).source_metadata == {}


# --- store round-trip: JSON persists and reloads without drift -------------------------------


def test_chunk_record_round_trips_through_json():
    rec = _record()
    reloaded = ChunkRecord.model_validate_json(rec.model_dump_json())
    assert reloaded == rec
    assert all(isinstance(k, int) for k in reloaded.sparse_vector)
