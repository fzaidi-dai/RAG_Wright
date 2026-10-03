"""Generic document-parse surface (EP-API-6b): the corpus-seam `SourceDocument` contract + the docling-parse
helpers that build one from raw bytes.

DOMAIN-FREE on purpose. It uses only the generic docling parser (`capabilities.parsing`) and the generic text
extraction (`corpus.document_parser`) -- nothing contract/clause/edgar -- so the engine's public parse API
(`api.parse_document`) depends on THIS, not on the contract ingestion pipeline (which would drag the whole
contract reference pack into any caller just to parse a PDF). A byte-source corpus adapter for ANY domain builds
a structure-bearing `SourceDocument` the same way. (Relocated out of `subgraphs/contract_ingestion_pipeline`,
which now re-exports these for its existing importers.)
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from pydantic import BaseModel

from rag_wright.capabilities.parsing import ParsedDocument

_INGEST_PARSE_DEADLINE_S = 600.0  # per-document parse ceiling (a degraded multi-page doc escalated to the VLM)


class SourceDocument(BaseModel):
    """One document to ingest: its canonical `source_doc_id` (HYG-1), its already-parsed text, and optional
    per-corpus metadata (e.g. CUAD annotated parties, ACORD pre-segmented spans) the stages may consult."""

    source_doc_id: str
    text: str
    metadata: dict = {}
    # CHUNK-7 (ADR-0058, issue 0004): the REAL docling parse (structure preserved), set by a BYTE-source adapter
    # (or `parsed_source_document`). When present, the chunk stage uses it so the structural pass fires on the
    # document's actual headings; when None (genuinely text-only input) the chunker falls back to a text parse.
    parsed: Optional[ParsedDocument] = None
    # 0009-WIRE2: pages the tiered OCR could not read even after VLM escalation (a degraded scan) -- surfaced as an
    # `ocr` PARTIAL in the IngestionReport, never silently ingested as gibberish.
    ocr_unreadable_pages: list[int] = []


def parsed_source_document(
    source_doc_id: str, name: str, data: bytes, *, cache_dir: Any, metadata: Optional[dict] = None
) -> SourceDocument:
    """Build a STRUCTURE-BEARING `SourceDocument` from raw document BYTES (PDF/DOCX/HTML/MD): docling-parse ONCE
    (content-hash gated + cached), carry the `DoclingDocument` on `.parsed` (so the chunker's structural pass
    fires on real headings), and set `.text` to the flattened text (for the text-consuming stages). This is how a
    byte-source corpus adapter -- or the product (RuleWright), which hand-builds its ingest -- feeds a real
    document to the engine; a plain-text `SourceDocument` (no `.parsed`) still uses the text fallback."""
    import hashlib
    import json
    from pathlib import Path

    from rag_wright.capabilities.parsing import TieredOCRParser, load_document
    from rag_wright.corpus.document_parser import document_to_text, parse_document_bytes

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    content_hash = hashlib.sha256(data).hexdigest()
    manifest_path = cache_dir / f"{source_doc_id}.{content_hash[:16]}.json"
    ocr_sidecar = cache_dir / f"{source_doc_id}.{content_hash[:16]}.ocr.json"  # the OCR verdict, cached alongside
    parsed = ParsedDocument(
        source_doc_id=source_doc_id, content_hash=content_hash, manifest_path=str(manifest_path))
    if manifest_path.exists():  # content-hash gate: parse once (restore the OCR verdict from the sidecar)
        document = load_document(parsed)
        unreadable = json.loads(ocr_sidecar.read_text()) if ocr_sidecar.exists() else []
    else:  # 0009-WIRE2: tiered OCR -- capture which pages stayed unreadable even after VLM, so they surface PARTIAL
        tiered = TieredOCRParser()
        document = parse_document_bytes(name, data, parser=tiered)
        document.save_as_json(manifest_path)
        unreadable = list(tiered.report.unreadable_pages)
        ocr_sidecar.write_text(json.dumps(unreadable))
    return SourceDocument(
        source_doc_id=source_doc_id, text=document_to_text(document), parsed=parsed, metadata=metadata or {},
        ocr_unreadable_pages=unreadable)


async def aparsed_source_document(
    source_doc_id: str, name: str, data: bytes, *, cache_dir: Any, metadata: Optional[dict] = None,
    deadline_s: float = _INGEST_PARSE_DEADLINE_S,
) -> SourceDocument:
    """The ASYNC, deadline-bounded twin of `parsed_source_document` (ADR-0057) -- STABLE PUBLIC API. Runs the sync
    build (docling parse + the tiered OCR/VLM escalation, the slowest call in the pipeline) OFF the event loop
    (`to_thread`) under an `asyncio.timeout`, so a hand-built async ingest can parse a document into the
    structure-bearing `SourceDocument` the chunker needs WITHOUT reimplementing the wrapper (or blocking the loop).
    Same caveat as every `to_thread` bound: the deadline unblocks the CALLER; the docling worker thread finishes in
    the background (true cancellation would route the vision call through the async model seam)."""
    async with asyncio.timeout(deadline_s):
        return await asyncio.to_thread(
            parsed_source_document, source_doc_id, name, data, cache_dir=cache_dir, metadata=metadata)
