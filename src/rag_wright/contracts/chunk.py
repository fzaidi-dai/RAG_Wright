"""The chunk record contract (FR-I.3, FR-S.1).

One record per chunk in the hybrid retrieval index. It holds the `chunk_id`, the summary, the
dense-over-summary vector, the sparse-over-full-text vector, extracted keywords and entities, and
source metadata. The record does not hold the raw full chunk text: FR-I.3 enumerates the summary
plus vectors, and the full text lives in the parse manifest keyed by `chunk_id` (FR-I.1).

The load-bearing part of this contract is the vector shapes. They must match what BGE-M3 produces
(the embedding capability, T19) and what the ArcadeDB dense `LSM_VECTOR` and sparse
`LSM_SPARSE_VECTOR` indexes bind (the store seam, T13), so the dense dimension and the sparse
key/value ranges are validated here.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, field_validator

from rag_wright.contracts.identifiers import ChunkId

# BAAI/bge-m3 dense embedding dimension. The dense vector is computed over the summary (FR-I.3);
# the ArcadeDB dense LSM_VECTOR index (T13) binds this dimension.
BGE_M3_DENSE_DIM = 1024

# Metadata values are kept to filterable JSON scalars so the store can index and filter on them
# (metadata filters, FR-Q.1).
MetadataValue = str | int | float | bool


class ChunkRecord(BaseModel):
    """A chunk's record in the retrieval index (FR-I.3, FR-S.1)."""

    chunk_id: ChunkId
    summary: str
    dense_vector: list[float]  # dense over the summary; length == BGE_M3_DENSE_DIM
    sparse_vector: dict[int, float]  # sparse over full text: token-id index -> non-negative weight
    keywords: list[str] = []
    # Unresolved surface-form entity mentions for retrieval metadata only. These are NOT resolved
    # and must NOT be joined to the graph's canonical entity_ids; resolution to entity_id happens
    # later (FR-C.7) and canonical entities live on graph nodes (T24). Naming this entity_mentions
    # (not entities) keeps the pre-resolution side of the fragmentation seam unambiguous.
    entity_mentions: list[str] = []
    source_metadata: dict[str, MetadataValue] = {}

    @field_validator("summary")
    @classmethod
    def _summary_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("summary must be non-empty")
        return v

    @field_validator("dense_vector")
    @classmethod
    def _dense_shape(cls, v: list[float]) -> list[float]:
        if len(v) != BGE_M3_DENSE_DIM:
            raise ValueError(f"dense_vector must have length {BGE_M3_DENSE_DIM}, got {len(v)}")
        if not all(math.isfinite(x) for x in v):
            raise ValueError("dense_vector must contain only finite values")
        return v

    @field_validator("sparse_vector")
    @classmethod
    def _sparse_ranges(cls, v: dict[int, float]) -> dict[int, float]:
        for token_id, weight in v.items():
            if token_id < 0:
                raise ValueError(f"sparse_vector token ids must be >= 0, got {token_id}")
            if not math.isfinite(weight) or weight < 0:
                raise ValueError(f"sparse_vector weights must be finite and >= 0, got {weight}")
        return v

    @field_validator("keywords", "entity_mentions")
    @classmethod
    def _no_blank_items(cls, v: list[str]) -> list[str]:
        if any(not item.strip() for item in v):
            raise ValueError("keywords and entity_mentions must not contain blank strings")
        return v
