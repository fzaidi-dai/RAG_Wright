"""Operative-span store record (FR-R, ADR-0025).

One record per operative span in the ArcadeDB `Span` hybrid index. Unlike a `ChunkRecord` (dense over the
summary, text kept in a sidecar), the span IS the small retrieval unit, so the record carries the span text:
the dense vector is over the span, the sparse vector is over the span, the `function` is the classifier tag
(T56), and the parent pointer (`parent_chunk_id`) locates the full parent unit for the
rerank stage. `span_id` embeds the parent (identifier rule).
"""

from __future__ import annotations

import json
import math

from pydantic import BaseModel, field_validator, model_validator

from typing import TYPE_CHECKING

from rag_wright.contracts.chunk import BGE_M3_DENSE_DIM

if TYPE_CHECKING:
    from rag_wright.contracts.ingestion import Span


class SpanRecord(BaseModel):
    """One operative-span record for the `Span` hybrid index (dense + sparse over the span text).

    CUAD-highlighting fields (CU-A1, ADR-0029) are OPTIONAL/defaulted so the ACORD span leg (which does not
    set them) is unaffected: `document_id` is the source-document id used by the within-document filter
    (derivable from `parent_chunk_id` but stored explicitly for an indexed WHERE); `doc_start`/`doc_end` are
    document-absolute character offsets of the span (the citation the app highlights on); `page`/`bbox` are the
    optional PDF-overlay provenance (Docling-supplied where available). `parent_chunk_id` is the parent-clause
    pointer (a clause == a chunk), so no separate clause_id field is added.
    """

    model_config = {"frozen": True, "extra": "forbid"}  # ING-8d: an old field name fails loudly

    span_id: str  # "{parent_chunk_id}#{span_index}"
    parent_chunk_id: str  # the parent chunk id; the span<->chunk (or unit) link
    span_index: int
    text: str
    primary_tag: str = ""  # the span tagger's PRIMARY tag (ING-8d; was `function`); "" until tagged. == tags[0].
    tags: list[str] = []  # the top-k soft tags, primary-first (ING-8d; was `functions`), so a span is discoverable
    #   under several tags (ADR-0114 multi-tag soft-tagging).
    dense_vector: list[float]  # dense over the span; length == BGE_M3_DENSE_DIM
    sparse_vector: dict[int, float]  # sparse over the span: token-id -> non-negative weight
    document_id: str = ""  # the source-document id (within-document filter; ING-8d, was `contract_id`)
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


def to_span_record(
    op: "Span",
    *,
    document_id: str,
    chunk_doc_start: int,
    dense_vector: list[float],
    sparse_vector: dict[int, float],
    primary_tag: str = "",
    tags: list[str] | None = None,
) -> SpanRecord:
    """CU-B2 (ADR-0029): OperativeSpan -> SpanRecord with DOCUMENT-ABSOLUTE offsets.

    Composes `doc_start = chunk_doc_start + op.start`, `doc_end = chunk_doc_start + op.end` (the span's
    clause-relative offsets shifted by the parent chunk's offset in the canonical document text, CU-B1). The
    RAW span text (`op.text = body[start:end]`) is stored -- NOT stripped -- so the citation invariant
    `canonical_document_text[doc_start:doc_end] == span.text` holds byte-faithfully. The caller may embed over
    `op.text.strip()`; the stored text stays raw for the highlight.
    """
    return SpanRecord(
        span_id=op.span_id,
        parent_chunk_id=op.parent_chunk_id,
        span_index=op.span_index,
        text=op.text,
        primary_tag=primary_tag,
        tags=list(tags) if tags else ([primary_tag] if primary_tag else []),
        dense_vector=dense_vector,
        sparse_vector=sparse_vector,
        document_id=document_id,
        doc_start=chunk_doc_start + op.start,
        doc_end=chunk_doc_start + op.end,
        page=(op.pages[0] if op.pages else None),  # issue 0032: FIRST page for the singular highlight field
        pages=list(op.pages),  # ALL pages the span overlaps (cross-page clause -> a list)
        bbox=op.bbox,  # best-effort single-item box
    )


def decode_bbox(raw) -> tuple[float, float, float, float] | None:
    """issue 0032: the store keeps a span's bbox as a JSON `[l,t,r,b]` string (best-effort); decode it to a
    `(l, t, r, b)` tuple, or None. The single canonical decoder (generic since ING-8b)."""
    if not raw:
        return None
    try:
        vals = json.loads(raw) if isinstance(raw, str) else raw
        return (float(vals[0]), float(vals[1]), float(vals[2]), float(vals[3])) if vals else None
    except (ValueError, TypeError, IndexError):
        return None
