"""T19: embedding (BGE-M3) — FR-C.2, FR-I.3, FR-I.6.

Hermetic tests inject a stub `Embedder` (no model) to prove: a dense vector is produced over the
chunk summary and a native sparse vector over the full chunk text (from the chunk, not re-derived),
the output shapes match the T3 chunk-record contract, and the FR-I.6 decoupling property holds — the
capability is concurrent and non-blocking, its encode calls go through a poolable boundary, and a
semaphore applies backpressure (a concurrency test bounds in-flight calls). The real BGE-M3 encode is
opt-in (`-m embed`).
"""

from __future__ import annotations

import threading
import time

import pytest

from rag_wright.capabilities.embedding import (
    ChunkEmbedding,
    embed_chunks_sync,
    register_embedding,
)
from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.capabilities.rlm_chunking import Chunk
from rag_wright.contracts.chunk import BGE_M3_DENSE_DIM


def _chunk(index: int) -> Chunk:
    return Chunk(
        chunk_id=f"contract_a:{index}:" + "0" * 64,
        chunk_index=index,
        text=f"full text of chunk {index}, governed by Delaware law",
        summary=f"summary {index}",
        token_estimate=10,
    )


class _StubEmbedder:
    """Records inputs and tracks peak in-flight calls (to prove dense/sparse routing + backpressure)."""

    def __init__(self, delay: float = 0.02) -> None:
        self.delay = delay
        self.dense_inputs: list[str] = []
        self.sparse_inputs: list[str] = []
        self._inflight = 0
        self.max_inflight = 0
        self._lock = threading.Lock()

    def _work(self) -> None:
        with self._lock:
            self._inflight += 1
            self.max_inflight = max(self.max_inflight, self._inflight)
        time.sleep(self.delay)
        with self._lock:
            self._inflight -= 1

    def encode_dense(self, text: str) -> list[float]:
        self.dense_inputs.append(text)
        self._work()
        return [0.0] * BGE_M3_DENSE_DIM

    def encode_sparse(self, text: str) -> dict[int, float]:
        self.sparse_inputs.append(text)
        self._work()
        return {1: 0.5, 7: 0.25}


def test_dense_over_summary_and_sparse_over_full_text():
    chunk = _chunk(0)
    embedder = _StubEmbedder()

    [embedding] = embed_chunks_sync([chunk], embedder=embedder)

    assert isinstance(embedding, ChunkEmbedding)
    assert embedding.chunk_id == chunk.chunk_id
    assert embedder.dense_inputs == [chunk.summary]  # dense leg reads the summary
    assert embedder.sparse_inputs == [chunk.text]  # sparse leg reads the full chunk text


def test_output_shapes_match_the_chunk_record_contract():
    [embedding] = embed_chunks_sync([_chunk(0)], embedder=_StubEmbedder())
    assert len(embedding.dense_vector) == BGE_M3_DENSE_DIM  # 1024
    assert isinstance(embedding.sparse_vector, dict)
    assert all(isinstance(k, int) for k in embedding.sparse_vector)  # int token-id keys


def test_wrong_dense_dimension_is_rejected():
    class _BadDim(_StubEmbedder):
        def encode_dense(self, text: str) -> list[float]:
            return [0.0] * 10  # not BGE_M3_DENSE_DIM

    with pytest.raises(ValueError):
        embed_chunks_sync([_chunk(0)], embedder=_BadDim())


def test_backpressure_bounds_concurrent_encode_calls():
    chunks = [_chunk(i) for i in range(8)]

    embedder = _StubEmbedder()
    embed_chunks_sync(chunks, embedder=embedder, max_concurrency=3)
    assert embedder.max_inflight == 3  # the semaphore bounds in-flight calls, and reaches the bound

    serial = _StubEmbedder()
    embed_chunks_sync(chunks, embedder=serial, max_concurrency=1)
    assert serial.max_inflight == 1  # concurrency 1 => strictly serial


def test_concurrent_is_faster_than_serial():
    chunks = [_chunk(i) for i in range(8)]

    t0 = time.time()
    embed_chunks_sync(chunks, embedder=_StubEmbedder(delay=0.03), max_concurrency=4)
    concurrent = time.time() - t0

    t1 = time.time()
    embed_chunks_sync(chunks, embedder=_StubEmbedder(delay=0.03), max_concurrency=1)
    serial = time.time() - t1

    assert concurrent < serial  # non-blocking: work overlaps


def test_embedding_registers_under_frc2_as_a_function():
    reg = CapabilityRegistry()
    register_embedding(reg)
    registration = reg.get("embedding")
    assert registration.kind == "function"
    assert registration.contract is ChunkEmbedding
    assert registration.skeleton.identifier == "urn:air:dreamai.io:rag_wright:embedding"


@pytest.mark.embed
def test_real_bge_m3_produces_dense_and_sparse():
    from rag_wright.capabilities.embedding import BGEM3Embedder

    embedder = BGEM3Embedder()
    [embedding] = embed_chunks_sync([_chunk(0)], embedder=embedder)

    assert len(embedding.dense_vector) == BGE_M3_DENSE_DIM
    assert embedding.sparse_vector  # non-empty native sparse weights
    assert all(isinstance(k, int) and v >= 0 for k, v in embedding.sparse_vector.items())
