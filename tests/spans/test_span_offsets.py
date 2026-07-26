"""CU-B2: document-absolute span offsets compose (chunk offset + clause-relative) and round-trip."""

from __future__ import annotations

from rag_wright.contracts.chunk import BGE_M3_DENSE_DIM
from rag_wright.spans.segment import segment_clause, to_span_record


def _dense() -> list[float]:
    return [0.0] * BGE_M3_DENSE_DIM


def test_document_absolute_span_offsets_round_trip():
    chunk_body = ("9.1 No Consequential Damages. Neither party is liable for indirect loss. "
                  "9.2 Cap. Liability is limited to the fees paid.")
    canonical = "PREAMBLE text here.\n\n" + chunk_body + "\n\nEXHIBIT A"
    chunk_doc_start = canonical.index(chunk_body)  # the parent chunk's offset in the canonical doc text

    ops = segment_clause("c:0:h", chunk_body)
    recs = [to_span_record(op, contract_id="C1", chunk_doc_start=chunk_doc_start,
                           dense_vector=_dense(), sparse_vector={1: 0.5}) for op in ops]

    for sr, op in zip(recs, ops):
        # the CUAD citation invariant: doc-absolute offsets slice the canonical text back to the RAW span text
        assert canonical[sr.doc_start : sr.doc_end] == sr.text == op.text
        assert sr.contract_id == "C1"
    # doc-space contiguity + byte-faithful tiling of the chunk region
    assert recs[0].doc_start == chunk_doc_start
    assert recs[-1].doc_end == chunk_doc_start + len(chunk_body)
    assert "".join(sr.text for sr in recs) == chunk_body
    for a, b in zip(recs, recs[1:]):  # spans are contiguous in doc space (no gap/overlap)
        assert a.doc_end == b.doc_start


def test_stored_text_is_raw_slice_not_stripped():
    body = "Section 1. Alpha provision. Beta provision follows."
    for op in segment_clause("c:0:h", body):
        sr = to_span_record(op, contract_id="C", chunk_doc_start=0,
                            dense_vector=_dense(), sparse_vector={1: 0.1})
        assert sr.text == body[sr.doc_start : sr.doc_end]  # raw (may carry a leading space), not stripped
