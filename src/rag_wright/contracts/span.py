"""Operative-span store record (FR-R, ADR-0025).

One record per operative span in the ArcadeDB `Span` hybrid index. Unlike a `ChunkRecord` (dense over the
summary, text kept in a sidecar), the span IS the small retrieval unit, so the record carries the span text:
the dense vector is over the span, the sparse vector is over the span, the `function` is the classifier tag
(T56), and the parent pointer (`parent_chunk_id` + the clause's OKF path) locates the full clause for the
rerank stage. `span_id` embeds the parent (identifier rule).
"""

from __future__ import annotations

import math

from pydantic import BaseModel, field_validator

from rag_wright.contracts.chunk import BGE_M3_DENSE_DIM


class SpanRecord(BaseModel):
    """One operative-span record for the `Span` hybrid index (dense + sparse over the span text)."""

    model_config = {"frozen": True}

    span_id: str  # "{parent_chunk_id}#{span_index}"
    parent_chunk_id: str
    parent_okf_path: str = ""  # where the parent clause lives in the clause OKF bundle
    span_index: int
    text: str
    function: str = ""  # the function-classifier tag (T56); "" until classified
    dense_vector: list[float]  # dense over the span; length == BGE_M3_DENSE_DIM
    sparse_vector: dict[int, float]  # sparse over the span: token-id -> non-negative weight

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
