"""T20: chunk write + incremental upsert (content-hash gated) — FR-I.3, FR-I.5.

Hermetic tests use an in-memory store binding the extended `Store` seam to prove the capability's
logic: a chunk record upserts by `chunk_id` (re-write updates, no duplicate), an unchanged document
does effectively no work (content-hash gate), and a failed document lands in the dead-letter queue
while a resumed run continues from per-chunk checkpoints. The real ArcadeDB upsert is opt-in
(`-m store`). Chunk write is a seam-bound ingestion pipeline step, not a query-discovered capability,
so it has no ARD manifest (SPEC section 5).
"""

from __future__ import annotations

from typing import Optional

import pytest

from rag_wright.capabilities.chunk_write import ChunkWriter, to_chunk_record
from rag_wright.capabilities.embedding import ChunkEmbedding
from rag_wright.capabilities.rlm_chunking import Chunk
from rag_wright.contracts.chunk import BGE_M3_DENSE_DIM, ChunkRecord
from rag_wright.contracts.identifiers import ChunkId
from rag_wright.store.chunk_text import ChunkTextStore

_SRC = "contract_a"


def _writer(store, tmp_path):
    """A ChunkWriter with a real chunk-text sidecar under tmp_path (T40)."""
    return ChunkWriter(
        store, text_store=ChunkTextStore(tmp_path / "chunk_text"), checkpoint_dir=tmp_path
    )


def _texts(chunks: list[Chunk]) -> dict[str, str]:
    """The chunk_id -> full text map the write leg persists to the sidecar (T40)."""
    return {c.chunk_id: c.text for c in chunks}


def _chunk(index: int, text: str | None = None) -> Chunk:
    text = text or f"full text of section {index}, governed by Delaware law"
    return Chunk(
        chunk_id=ChunkId.of(_SRC, index, text).value,
        chunk_index=index,
        text=text,
        summary=f"summary {index}",
        token_estimate=10,
    )


def _embedding(chunk: Chunk) -> ChunkEmbedding:
    return ChunkEmbedding(
        chunk_id=chunk.chunk_id, dense_vector=[0.0] * BGE_M3_DENSE_DIM, sparse_vector={1: 0.5, 7: 0.2}
    )


def _records(chunks: list[Chunk]) -> list[ChunkRecord]:
    return [to_chunk_record(c, _embedding(c), source_doc_id=_SRC) for c in chunks]


class _InMemoryStore:
    """A second `Store` implementation (proves swappability) that records writes for the tests."""

    def __init__(self) -> None:
        self._chunks: dict[str, ChunkRecord] = {}
        self.upsert_calls = 0

    def ensure_schema(self) -> None: ...
    def type_names(self) -> set[str]:
        return {"Chunk", "Entity"}
    def property_names(self, type_name: str) -> set[str]:
        return set()
    def index_names(self) -> set[str]:
        return set()
    def ping(self) -> bool:
        return True
    def close(self) -> None: ...

    def upsert_chunk(self, record: ChunkRecord) -> None:
        self.upsert_calls += 1
        self._chunks[record.chunk_id.value] = record

    def get_chunk(self, chunk_id: str) -> Optional[dict]:
        record = self._chunks.get(chunk_id)
        return {"chunk_id": chunk_id, "summary": record.summary} if record else None

    def chunk_count(self) -> int:
        return len(self._chunks)


class _FailOnceStore(_InMemoryStore):
    """Fails on the nth upsert of a run exactly once, to exercise the dead-letter + resume path."""

    def __init__(self, fail_on_call: int) -> None:
        super().__init__()
        self._fail_on = fail_on_call
        self._armed = True

    def upsert_chunk(self, record: ChunkRecord) -> None:
        if self._armed and self.upsert_calls + 1 == self._fail_on:
            self._armed = False
            raise RuntimeError("simulated store write failure")
        super().upsert_chunk(record)


def test_upsert_dedups_by_chunk_id():
    store = _InMemoryStore()
    [record] = _records([_chunk(0)])

    store.upsert_chunk(record)
    store.upsert_chunk(record)  # same chunk_id again

    assert store.chunk_count() == 1  # updated in place, not duplicated


def test_content_hash_gate_skips_unchanged_document(tmp_path):
    store = _InMemoryStore()
    writer = _writer(store, tmp_path)
    chunks = [_chunk(0), _chunk(1)]
    records = _records(chunks)

    first = writer.write_document(_SRC, content_hash="h1", records=records, texts=_texts(chunks))
    second = writer.write_document(_SRC, content_hash="h1", records=records, texts=_texts(chunks))

    assert first.status == "written" and first.written_count == 2
    assert second.status == "skipped" and second.written_count == 0
    assert store.upsert_calls == 2  # the second run does effectively no work


def test_changed_content_rewrites(tmp_path):
    store = _InMemoryStore()
    writer = _writer(store, tmp_path)

    first = [_chunk(0)]
    writer.write_document(_SRC, content_hash="h1", records=_records(first), texts=_texts(first))
    changed = [_chunk(0, "new text")]
    result = writer.write_document(
        _SRC, content_hash="h2", records=_records(changed), texts=_texts(changed)
    )

    assert result.status == "written"
    assert store.upsert_calls == 2  # a new content hash re-writes


def test_failure_dead_letters_then_resumes_from_checkpoint(tmp_path):
    chunks = [_chunk(0), _chunk(1), _chunk(2)]
    records = _records(chunks)
    texts = _texts(chunks)

    failing = _FailOnceStore(fail_on_call=2)  # first chunk writes, second fails
    writer = _writer(failing, tmp_path)
    result = writer.write_document(_SRC, content_hash="h1", records=records, texts=texts)

    assert result.status == "dead_lettered"
    assert failing.chunk_count() == 1  # only the first chunk landed
    assert writer.dead_letter_ids() == {_SRC}

    # a resumed run continues from the per-chunk checkpoint: the first chunk is skipped, the rest write
    resumed = writer.write_document(_SRC, content_hash="h1", records=records, texts=texts)
    assert resumed.status == "written"
    assert failing.chunk_count() == 3
    assert failing.upsert_calls == 3  # chunk 0 (first run) + chunks 1,2 (resume); chunk 0 not rewritten
    assert writer.dead_letter_ids() == set()  # cleared on success


def test_sidecar_text_persists_under_the_same_gate_as_the_index(tmp_path):
    """The sidecar write shares the index's content-hash gate: a written chunk has recoverable text,
    and a gate-skipped (unchanged) document writes neither index nor sidecar (one decision, no drift)."""
    store = _InMemoryStore()
    text_store = ChunkTextStore(tmp_path / "chunk_text")
    writer = ChunkWriter(store, text_store=text_store, checkpoint_dir=tmp_path)
    chunks = [_chunk(0), _chunk(1)]
    records = _records(chunks)

    writer.write_document(_SRC, content_hash="h1", records=records, texts=_texts(chunks))

    # every chunk the index upserted has its full text recoverable from the sidecar, by chunk_id
    for chunk in chunks:
        assert text_store.get(chunk.chunk_id) == chunk.text
        assert store.get_chunk(chunk.chunk_id) is not None  # index holds the summary, sidecar the text

    # the same gate governs both: an unchanged re-write skips the sidecar exactly as it skips the index
    text_store._path(_SRC).write_text("{}", encoding="utf-8")  # tamper: prove no rewrite happens
    second = writer.write_document(_SRC, content_hash="h1", records=records, texts=_texts(chunks))
    assert second.status == "skipped"
    assert text_store.get(chunks[0].chunk_id) is None  # skipped -> the sidecar was not re-written


def test_write_document_rejects_records_without_their_text(tmp_path):
    """Consistency guard: a chunk cannot land in the index without its text in the sidecar."""
    store = _InMemoryStore()
    writer = _writer(store, tmp_path)
    chunks = [_chunk(0), _chunk(1)]
    records = _records(chunks)
    incomplete = {chunks[0].chunk_id: chunks[0].text}  # chunk 1's text missing

    with pytest.raises(ValueError):
        writer.write_document(_SRC, content_hash="h1", records=records, texts=incomplete)

    assert store.upsert_calls == 0  # nothing persisted to the index either


def test_to_chunk_record_assembles_from_chunk_and_embedding():
    chunk = _chunk(0)
    record = to_chunk_record(chunk, _embedding(chunk), source_doc_id=_SRC)
    assert isinstance(record, ChunkRecord)
    assert record.chunk_id.value == chunk.chunk_id
    assert record.summary == chunk.summary
    assert len(record.dense_vector) == BGE_M3_DENSE_DIM


def test_to_chunk_record_rejects_mismatched_ids():
    chunk = _chunk(0)
    wrong = ChunkEmbedding(
        chunk_id="other:0:" + "0" * 64, dense_vector=[0.0] * BGE_M3_DENSE_DIM, sparse_vector={1: 0.5}
    )
    with pytest.raises(ValueError):
        to_chunk_record(chunk, wrong, source_doc_id=_SRC)


# --- live ArcadeDB upsert (opt-in) ---------------------------------------------------------------

_TEST_DB = "ragwright_chunkwrite_test"


@pytest.fixture
def arcadedb_store():
    from rag_wright.store.arcadedb import ArcadeDBStore

    store = ArcadeDBStore.from_env(database=_TEST_DB, reset=True)
    store.ensure_schema()
    yield store
    store.drop()
    store.close()


@pytest.mark.store
def test_real_arcadedb_upsert_dedups_and_updates(tmp_path, arcadedb_store):
    writer = _writer(arcadedb_store, tmp_path)
    chunk = _chunk(0)
    record = to_chunk_record(chunk, _embedding(chunk), source_doc_id=_SRC)

    writer.write_document(_SRC, content_hash="h1", records=[record], texts=_texts([chunk]))
    assert arcadedb_store.chunk_count() == 1
    assert arcadedb_store.get_chunk(chunk.chunk_id)["summary"] == "summary 0"

    # re-writing the same chunk_id updates in place (no duplicate), verified against the live store
    arcadedb_store.upsert_chunk(record.model_copy(update={"summary": "summary 0 (revised)"}))
    assert arcadedb_store.chunk_count() == 1
    assert arcadedb_store.get_chunk(chunk.chunk_id)["summary"] == "summary 0 (revised)"
