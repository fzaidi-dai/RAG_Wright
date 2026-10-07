"""EP-API-6 (ADR-0117): the PDF/docling ingest entry point on the engine API. `parse_document` / `aparse_document`
turn a file PATH into a STRUCTURE-BEARING `SourceDocument` (docling-parsed, `.parsed` set) through the API, so the
full ingestion pipeline incl docling runs via `rag_wright.api` -- not only the text-fallback `source_document`.

Hermetic tests stub the engine byte-builder to prove the path->(name, bytes, cache_dir, metadata) plumbing; the live
test runs a real (born-digital, offline) docling parse of the committed fixture."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from rag_wright.api import aparse_document, parse_document, source_document


def test_source_document_stays_text_only():
    sd = source_document("ACME_MSA", text="Section 8. Limitation of Liability.")
    assert sd.source_doc_id == "ACME_MSA" and sd.parsed is None  # the text path sets no docling structure


def test_parse_document_plumbs_path_into_the_byte_builder(monkeypatch, tmp_path):
    seen = {}

    def _stub(source_doc_id, name, data, *, cache_dir, metadata=None, include_hidden_sheets):
        seen.update(id=source_doc_id, name=name, data=data, cache_dir=str(cache_dir), metadata=metadata,
                    hidden=include_hidden_sheets)
        from rag_wright.subgraphs.contract_ingestion_pipeline import SourceDocument
        return SourceDocument(source_doc_id=source_doc_id, text="x")

    monkeypatch.setattr(
        "rag_wright.capabilities.document_parse.parsed_source_document", _stub)
    pdf = tmp_path / "ACME Contract.pdf"
    pdf.write_bytes(b"%PDF-1.4 bytes")
    parse_document("ACME_MSA", pdf, cache_dir=tmp_path / "cache", metadata={"tier": "gold"})
    assert seen["id"] == "ACME_MSA" and seen["name"] == "ACME Contract.pdf"
    assert seen["data"] == b"%PDF-1.4 bytes" and seen["metadata"] == {"tier": "gold"}
    assert seen["cache_dir"].endswith("cache")
    assert seen["hidden"] is True  # ING-4a: hidden spreadsheet sheets are ingested by default


def test_aparse_document_plumbs_path_into_the_async_byte_builder(monkeypatch, tmp_path):
    seen = {}

    async def _astub(source_doc_id, name, data, *, cache_dir, metadata=None, include_hidden_sheets):
        seen.update(id=source_doc_id, name=name, data=data, hidden=include_hidden_sheets)
        from rag_wright.subgraphs.contract_ingestion_pipeline import SourceDocument
        return SourceDocument(source_doc_id=source_doc_id, text="x")

    monkeypatch.setattr(
        "rag_wright.capabilities.document_parse.aparsed_source_document", _astub)
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 async")
    asyncio.run(aparse_document("DOC", pdf, cache_dir=tmp_path / "cache", include_hidden_sheets=False))
    assert seen["id"] == "DOC" and seen["name"] == "doc.pdf" and seen["data"] == b"%PDF-1.4 async"
    assert seen["hidden"] is False  # the skip choice reaches the builder


# --- live: a real docling parse through the API (born-digital fixture -> offline, no VLM/OpenRouter) ---

_FIXTURE = Path("tests/fixtures/table-bearing-contract.pdf")


@pytest.mark.skipif(not _FIXTURE.exists(), reason="PDF fixture not present")
def test_parse_document_real_pdf_is_structure_bearing(tmp_path):
    sd = parse_document("TABLE_CONTRACT", _FIXTURE, cache_dir=tmp_path)
    assert sd.source_doc_id == "TABLE_CONTRACT"
    assert sd.parsed is not None, "docling parse should set .parsed (structure-bearing), not fall back to text"
    assert sd.text and len(sd.text) > 50  # real flattened text came back
