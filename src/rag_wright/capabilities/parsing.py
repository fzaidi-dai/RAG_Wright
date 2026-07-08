"""Parsing capability (FR-C.1): source documents -> a clean structured representation, parsed once.

Docling's `DocumentConverter` turns a PDF, Office file, or scan into a `DoclingDocument` (reading
order, headings, sections, tables, OCR). Parsing is content-hash gated so a document is parsed once
and reused by chunking, embedding, and extraction: the structured representation is cached as JSON
(`DoclingDocument.save_as_json` / `load_from_json`) keyed by the source's content hash, so re-parsing
unchanged content is a cache hit and changed content re-parses.

The `DocumentConverter` sits behind a small `Parser` seam so the capability's cache/gate logic is
tested hermetically with a stub, and the real (model-loading) parse is exercised opt-in (`-m parse`).
Grounded against `docling.document_converter.DocumentConverter.convert` and
`docling_core.types.doc.document.DoclingDocument` (framework graph).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Protocol, runtime_checkable

from docling_core.types.doc.document import DoclingDocument
from pydantic import BaseModel, field_validator

from rag_wright.capabilities.registry import CapabilityRegistry

# Reused from the ChunkId scheme (T1): the delimiter-safe charset for a source_doc_id, so the id is
# citation/provenance-safe and consistent with `chunk_id`.
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


@runtime_checkable
class Parser(Protocol):
    """The document-conversion seam: turn a source path into a `DoclingDocument`."""

    def convert(self, source: Path) -> DoclingDocument: ...


class ParsedDocument(BaseModel):
    """The parsing capability's contract: a handle to the cached structured representation.

    The full `DoclingDocument` lives in the parse manifest at `manifest_path` (loaded via
    `load_document`); this record carries the identity and the content hash the pipeline gates on.
    """

    model_config = {"frozen": True}

    source_doc_id: str
    content_hash: str  # sha256 hex of the source bytes
    manifest_path: str

    @field_validator("source_doc_id")
    @classmethod
    def _safe_source_doc_id(cls, v: str) -> str:
        if not v or _SAFE.search(v):
            raise ValueError("source_doc_id must be non-empty and use only [A-Za-z0-9._-]")
        return v


class DoclingParser:
    """The real parser: Docling's `DocumentConverter`. Constructed lazily so importing the capability
    (and the hermetic tests) does not load Docling's models."""

    def __init__(self) -> None:
        from docling.document_converter import DocumentConverter

        self._converter = DocumentConverter()

    def convert(self, source: Path) -> DoclingDocument:
        return self._converter.convert(source).document


def _source_doc_id(source: Path) -> str:
    """A delimiter-safe id from the file stem (spaces/punctuation -> '_')."""
    return _SAFE.sub("_", source.stem)


def _content_hash(source: Path) -> str:
    return hashlib.sha256(source.read_bytes()).hexdigest()


def parse(source: Path, *, cache_dir: Path, parser: Parser) -> ParsedDocument:
    """Parse `source` into the cached structured representation, parsed once (content-hash gated).

    If a manifest for this content hash already exists, it is reused (no re-parse); otherwise the
    source is converted and the `DoclingDocument` is cached as JSON.
    """
    source_doc_id = _source_doc_id(source)
    content_hash = _content_hash(source)
    cache_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = cache_dir / f"{source_doc_id}.{content_hash[:16]}.json"

    if not manifest_path.exists():  # the content-hash gate: parse once
        document = parser.convert(source)
        document.save_as_json(manifest_path)

    return ParsedDocument(
        source_doc_id=source_doc_id,
        content_hash=content_hash,
        manifest_path=str(manifest_path),
    )


def load_document(parsed: ParsedDocument) -> DoclingDocument:
    """Load the full structured representation from the parse manifest."""
    return DoclingDocument.load_from_json(parsed.manifest_path)


def register_parsing(registry: CapabilityRegistry) -> None:
    """Register the parsing capability under FR-C.1 (`parsing`, an in-process `function`)."""
    registry.register(
        "parsing",
        contract=ParsedDocument,
        kind="function",
        display_name="Document parsing (Docling)",
    )
