"""Embedding capability (FR-C.2, FR-I.3): dense over the summary, native sparse over the full text.

From one BGE-M3 model, produce a dense vector over the chunk summary and a native sparse vector over
the full chunk text. The dense-over-summary + sparse-over-full-text split is the summary-miss
mitigation (a detail dropped from the summary is still recoverable through the sparse leg). Output
shapes match the T3 chunk-record contract (dense length `BGE_M3_DENSE_DIM`; sparse `dict[int, float]`,
BGE-M3's string token-id keys converted to ints).

FR-I.6 decoupling: embedding is a GPU-calling capability, so it is built to be called concurrently
and non-blocking. `embed_chunks` is async; each encode goes through `asyncio.to_thread` (a poolable
inference boundary, not a hardcoded synchronous call), and a semaphore applies backpressure so bulk
mode can saturate the GPU without unbounded in-flight work. Grounded on `FlagEmbedding.BGEM3FlagModel`
(the public BGE-M3 class; its inference engine is `M3Embedder`).
"""

from __future__ import annotations

import asyncio
import math
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, field_validator

from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.capabilities.rlm_chunking import Chunk
from rag_wright.contracts.chunk import BGE_M3_DENSE_DIM

DEFAULT_MAX_CONCURRENCY = 4  # in-flight encode budget (backpressure); tune per GPU at deploy


@runtime_checkable
class Embedder(Protocol):
    """The BGE-M3 inference seam: dense and native-sparse encoders."""

    def encode_dense(self, text: str) -> list[float]: ...
    def encode_sparse(self, text: str) -> dict[int, float]: ...


class ChunkEmbedding(BaseModel):
    """The embedding capability's output: one chunk's dense and sparse vectors."""

    model_config = {"frozen": True}

    chunk_id: str
    dense_vector: list[float]  # over the summary; length BGE_M3_DENSE_DIM
    sparse_vector: dict[int, float]  # over the full text; token-id -> non-negative weight

    @field_validator("dense_vector")
    @classmethod
    def _check_dense(cls, v: list[float]) -> list[float]:
        if len(v) != BGE_M3_DENSE_DIM:
            raise ValueError(f"dense_vector must have length {BGE_M3_DENSE_DIM}, got {len(v)}")
        if not all(math.isfinite(x) for x in v):
            raise ValueError("dense_vector must contain only finite values")
        return v

    @field_validator("sparse_vector")
    @classmethod
    def _check_sparse(cls, v: dict[int, float]) -> dict[int, float]:
        if any(weight < 0 for weight in v.values()):
            raise ValueError("sparse_vector weights must be non-negative")
        return v


def _resolve_device(device: str | None) -> str:
    """The device to run BGE-M3 on: explicit arg, else `EMBED_DEVICE`, else auto (Metal `mps` when available,
    else `cpu`). Auto-MPS offloads embedding from the CPU (freeing it for LegalBERT + extraction during ingest)
    and is ~2x faster; the vectors are bit-for-bit equivalent to CPU (verified cosine 1.0), so it is a pure
    speed choice, not a semantic one."""
    import os

    chosen = device or os.environ.get("EMBED_DEVICE")
    if chosen:
        return chosen
    try:
        import torch

        if torch.backends.mps.is_available():
            return "mps"
    except Exception:  # noqa: BLE001 - torch/mps probing must never break embedder construction
        pass
    return "cpu"


class BGEM3Embedder:
    """The real embedder: BGE-M3 via `FlagEmbedding.BGEM3FlagModel` (model loaded lazily)."""

    def __init__(self, model_name: str = "BAAI/bge-m3", *, use_fp16: bool = False,
                 device: str | None = None, batch_size: int = 64) -> None:
        from FlagEmbedding import BGEM3FlagModel

        self._batch_size = batch_size  # cross-item independent, so batching never changes a vector, only speed
        self._model = BGEM3FlagModel(model_name, use_fp16=use_fp16, devices=_resolve_device(device))

    def encode_dense(self, text: str) -> list[float]:
        out = self._model.encode([text], return_dense=True, return_sparse=False)
        return out["dense_vecs"][0].tolist()

    def encode_sparse(self, text: str) -> dict[int, float]:
        out = self._model.encode([text], return_dense=False, return_sparse=True)
        # lexical_weights is a Dict[str, float] keyed by string token ids; convert to int keys (T3).
        return {int(k): float(v) for k, v in out["lexical_weights"][0].items()}

    def encode_batch(self, texts: list[str]) -> tuple[list[list[float]], list[dict[int, float]]]:
        """Batched dense+sparse over many texts in ONE model call (the ingestion/population path):
        same per-text format as `encode_dense`/`encode_sparse`, amortizing the model overhead."""
        if not texts:
            return [], []
        out = self._model.encode(texts, return_dense=True, return_sparse=True, batch_size=self._batch_size)
        dense = [v.tolist() for v in out["dense_vecs"]]
        sparse = [{int(k): float(v) for k, v in lw.items()} for lw in out["lexical_weights"]]
        return dense, sparse


async def embed_chunks(
    chunks: list[Chunk],
    *,
    embedder: Embedder,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
) -> list[ChunkEmbedding]:
    """Embed chunks concurrently (dense over summary, sparse over full text), backpressured.

    Each chunk's encodes run in a thread (the poolable inference boundary); a semaphore bounds the
    in-flight work so bulk mode saturates the GPU without unbounded concurrency.
    """
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _embed(chunk: Chunk) -> ChunkEmbedding:
        async with semaphore:  # backpressure
            dense = await asyncio.to_thread(embedder.encode_dense, chunk.summary)
            sparse = await asyncio.to_thread(embedder.encode_sparse, chunk.text)
        return ChunkEmbedding(chunk_id=chunk.chunk_id, dense_vector=dense, sparse_vector=sparse)

    return list(await asyncio.gather(*(_embed(chunk) for chunk in chunks)))


def embed_chunks_sync(
    chunks: list[Chunk],
    *,
    embedder: Embedder,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
) -> list[ChunkEmbedding]:
    """Synchronous convenience for callers not already in an event loop."""
    return asyncio.run(embed_chunks(chunks, embedder=embedder, max_concurrency=max_concurrency))


def register_embedding(registry: CapabilityRegistry) -> None:
    """Register the embedding capability under FR-C.2 (`embedding`, an in-process `function`)."""
    registry.register(
        "embedding",
        contract=ChunkEmbedding,
        kind="model",  # BGE-M3 inference (CAP-REG-1)
        display_name="Embedding (BGE-M3)",
    )
