"""Operative-span store record (FR-R, ADR-0025).

One record per operative span in the ArcadeDB `Span` hybrid index. Unlike a `ChunkRecord` (dense over the
summary, text kept in a sidecar), the span IS the small retrieval unit, so the record carries the span text:
the dense vector is over the span, the sparse vector is over the span, the `function` is the classifier tag
(T56), and the parent pointer (`parent_chunk_id` + the clause's OKF path) locates the full clause for the
rerank stage. `span_id` embeds the parent (identifier rule).
"""

from __future__ import annotations

import math

from pydantic import BaseModel, field_validator, model_validator

from rag_wright.contracts.chunk import BGE_M3_DENSE_DIM


class SpanRecord(BaseModel):
    """One operative-span record for the `Span` hybrid index (dense + sparse over the span text).

    CUAD-highlighting fields (CU-A1, ADR-0029) are OPTIONAL/defaulted so the ACORD span leg (which does not
    set them) is unaffected: `contract_id` is the source-document id used by the within-contract typed filter
    (derivable from `parent_chunk_id` but stored explicitly for an indexed WHERE); `doc_start`/`doc_end` are
    document-absolute character offsets of the span (the citation the app highlights on); `page`/`bbox` are the
    optional PDF-overlay provenance (Docling-supplied where available). `parent_chunk_id` is the parent-clause
    pointer (a clause == a chunk), so no separate clause_id field is added.
    """

    model_config = {"frozen": True}

    span_id: str  # "{parent_chunk_id}#{span_index}"
    parent_chunk_id: str  # the parent CLAUSE id (a clause is a chunk); the span<->clause link
    parent_okf_path: str = ""  # where the parent clause lives in the clause OKF bundle
    span_index: int
    text: str
    function: str = ""  # the function-classifier tag (T56); "" until classified
    dense_vector: list[float]  # dense over the span; length == BGE_M3_DENSE_DIM
    sparse_vector: dict[int, float]  # sparse over the span: token-id -> non-negative weight
    contract_id: str = ""  # CU-A1: source contract/document id (within-contract typed filter)
    doc_start: int | None = None  # CU-A1: document-absolute char offset (citation); None on the ACORD leg
    doc_end: int | None = None  # CU-A1: exclusive
    page: int | None = None  # CU-A1: 1-based FIRST page for PDF-overlay highlight (== pages[0] when known)
    pages: list[int] = []  # issue 0032/CU-B5: ALL 1-based source pages this span overlaps (a clause can cross a
    #   page boundary); empty when the parse carried no page provenance (e.g. the text-only ingest leg)
    bbox: tuple[float, float, float, float] | None = None  # CU-A1: (left, top, right, bottom) on `page`, best-effort

    @model_validator(mode="after")
    def _check_offsets(self) -> "SpanRecord":
        if self.doc_start is not None and self.doc_start < 0:
            raise ValueError("doc_start must be non-negative")
        if self.doc_start is not None and self.doc_end is not None and self.doc_end < self.doc_start:
            raise ValueError(f"doc_end ({self.doc_end}) must be >= doc_start ({self.doc_start})")
        if self.page is not None and self.page < 1:
            raise ValueError("page is 1-based; must be >= 1")
        if any(p < 1 for p in self.pages):
            raise ValueError("pages are 1-based; each must be >= 1")
        return self

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
