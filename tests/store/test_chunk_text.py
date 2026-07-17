"""T40: the chunk-text sidecar (FR-I.3) — the per-chunk full text the index deliberately omits.

The retrieval index is dense-over-summary by design (`ChunkRecord` holds the summary and the vectors,
never the raw text). Synthesis (FR-Q.5) extracts over full chunk text, so the text captured at chunking
is persisted here keyed by `chunk_id` and rehydrated by `chunk_read` (T38). These hermetic tests prove
the store round-trips text by id, keys per source document, returns None for an absent id, and deletes a
document's text (the T34 delete-and-re-chunk seam).
"""

from __future__ import annotations

import pytest

from rag_wright.contracts.identifiers import ChunkId
from rag_wright.store.chunk_text import ChunkTextStore


def _cid(source_doc_id: str, index: int, text: str) -> ChunkId:
    return ChunkId.of(source_doc_id, index, text)


def test_put_then_get_round_trips_text_by_chunk_id(tmp_path):
    store = ChunkTextStore(tmp_path)
    cid = _cid("contract_a", 0, "full clause text, governed by Delaware law")

    store.put(cid, "full clause text, governed by Delaware law")

    assert store.get(cid.value) == "full clause text, governed by Delaware law"


def test_get_returns_none_for_absent_chunk_id(tmp_path):
    store = ChunkTextStore(tmp_path)
    assert store.get("contract_a:0:" + "0" * 64) is None


def test_text_is_keyed_per_source_document(tmp_path):
    store = ChunkTextStore(tmp_path)
    a = _cid("doc_a", 0, "text A")
    b = _cid("doc_b", 0, "text B")

    store.put(a, "text A")
    store.put(b, "text B")

    # same chunk_index, different documents, no collision — each recovers its own text
    assert store.get(a.value) == "text A"
    assert store.get(b.value) == "text B"


def test_multiple_chunks_of_one_document_share_the_file(tmp_path):
    store = ChunkTextStore(tmp_path)
    c0 = _cid("doc_a", 0, "text 0")
    c1 = _cid("doc_a", 1, "text 1")

    store.put(c0, "text 0")
    store.put(c1, "text 1")

    assert store.get(c0.value) == "text 0"
    assert store.get(c1.value) == "text 1"


def test_delete_document_removes_all_its_text(tmp_path):
    store = ChunkTextStore(tmp_path)
    c0 = _cid("doc_a", 0, "text 0")
    c1 = _cid("doc_a", 1, "text 1")
    store.put(c0, "text 0")
    store.put(c1, "text 1")

    store.delete_document("doc_a")

    assert store.get(c0.value) is None
    assert store.get(c1.value) is None


def test_delete_document_is_idempotent(tmp_path):
    store = ChunkTextStore(tmp_path)
    store.delete_document("never_written")  # no file, no error


def test_put_rejects_text_that_does_not_hash_to_the_chunk_id(tmp_path):
    """The sidecar's core promise is faithful text for a chunk_id. The chunk_id already carries the
    content hash of its text, so a put whose text does not hash to the id is a silent-wrong-text bug
    (loop index error, mismatched map) — rejected at the boundary, before it can be cited later."""
    store = ChunkTextStore(tmp_path)
    cid = _cid("contract_a", 0, "the real clause text")

    with pytest.raises(ValueError):
        store.put(cid, "a different clause entirely")  # does not hash to cid.content_hash

    assert store.get(cid.value) is None  # nothing persisted on the failed, mismatched write
