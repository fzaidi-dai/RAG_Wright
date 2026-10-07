"""CHUNK-7 (ADR-0058, issue 0004): carry docling structure to the chunker. Proves the ingestion stages use the
real docling parse when present (else the text fallback; ported to `IngestionStages.parsed` in ING-4c), and -- the A0004 close-out -- that a byte-source document
flows bytes -> parsed_source_document -> load_document -> the structural pass, which fires on the document's own
headings with ZERO model calls. The markdown parse is real docling (light: no OCR/models for .md)."""

from __future__ import annotations

from rag_wright.capabilities.parsing import ParsedDocument, load_document
from rag_wright.capabilities.rlm_chunking import StructuralModelFallbackDiscoverer, _validate_partition
from rag_wright.corpus.document_parser import _HEADING_LABELS
from rag_wright.ingestion.builder import IngestionStages
from rag_wright.subgraphs.contract_ingestion_pipeline import (
    SourceDocument,
    parsed_source_document,
)


class _RaisingFallback:
    """The model fallback must NOT be called on a well-structured document (structural handles it, zero model)."""

    def discover(self, document):
        raise AssertionError("the model fallback should not run on a structured document")

    async def adiscover(self, document):
        raise AssertionError("the model fallback should not run on a structured document")


def _stages(tmp_path):
    return IngestionStages(None, extractor=None, cache_dir=tmp_path)


def test_the_stages_use_the_real_parse_when_present(tmp_path):
    real = ParsedDocument(source_doc_id="x", content_hash="a" * 64, manifest_path="/does/not/matter")
    doc = SourceDocument(source_doc_id="x", text="body", parsed=real)
    assert _stages(tmp_path).parsed(doc) is real  # the document's own docling parse is used verbatim


def test_the_stages_fall_back_to_text_when_absent(tmp_path):
    doc = SourceDocument(source_doc_id="x", text="line one\nline two")  # no .parsed
    parsed = _stages(tmp_path).parsed(doc)
    assert isinstance(parsed, ParsedDocument) and parsed.source_doc_id == "x"
    loaded = load_document(parsed)
    assert [str(getattr(t, "label", "")) for t in loaded.texts] == ["text", "text"]  # text-only, no headings


def test_parsed_source_document_carries_structure_and_text(tmp_path):
    md = b"# Master Services Agreement\n\n## 1. Payment\nFees are due in 30 days.\n"
    sd = parsed_source_document("msa", "msa.md", md, cache_dir=tmp_path)
    assert sd.parsed is not None                       # structure carried
    assert "Fees are due" in sd.text                   # flattened text populated for the text stages
    labels = {str(getattr(t, "label", "")) for t in load_document(sd.parsed).texts}
    assert "section_header" in labels or "title" in labels  # docling headings survived to the chunker


def test_parsed_source_document_is_content_hash_gated(tmp_path):
    md = b"# A\n\n## 1. X\nbody.\n"
    a = parsed_source_document("d", "d.md", md, cache_dir=tmp_path)
    b = parsed_source_document("d", "d.md", md, cache_dir=tmp_path)  # second call reuses the cached manifest
    assert a.parsed.manifest_path == b.parsed.manifest_path and a.parsed.content_hash == b.parsed.content_hash


def test_a0004_bytes_to_structural_chunks_fire_on_headings_zero_model(tmp_path):
    # THE A0004 close-out: a real document (bytes) -> structure-preserving parse -> the structural pass cuts at
    # the document's OWN headings, deterministically, with NO model call (the fallback raises if reached).
    md = (b"# Master Services Agreement\n\n"
          b"## 1. Payment\nFees are due within thirty (30) days of invoice.\n\n"
          b"## 2. Termination\nEither party may terminate on 60 days notice.\n\n"
          b"## 3. Confidentiality\nEach party shall keep the other's information confidential.\n\n"
          b"## 4. Governing Law\nThis agreement is governed by the laws of Delaware.\n")
    sd = parsed_source_document("msa24", "msa.md", md, cache_dir=tmp_path)
    doc = load_document(sd.parsed)

    spans = StructuralModelFallbackDiscoverer(fallback=_RaisingFallback()).discover(doc)

    _validate_partition(spans, len(doc.texts))  # lossless: contiguous, gap-free, covers every item
    assert len(spans) >= 4  # the sections became separate chunks -- structure USED, not one flat blob
    assert all(getattr(doc.texts[s.start_index], "label", None) in _HEADING_LABELS for s in spans)  # clause-aligned
