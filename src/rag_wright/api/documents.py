"""EP-API-2b / EP-API-6 (ADR-0117): building a document to feed the ingestion capability.

`source_document(document_id, text=...)` makes an engine `SourceDocument` from plain text (the simplest path, no
parser needed). `parse_document` / `aparse_document` make a STRUCTURE-BEARING `SourceDocument` from a file PATH via
the engine's real docling parse (`.parsed` set), so the full ingestion pipeline incl docling runs through the API --
not only the text fallback. Heavy deps imported lazily so `import rag_wright.api` stays light."""
from __future__ import annotations

from pathlib import Path
from typing import Any


def source_document(document_id: str, *, text: str) -> Any:
    """A text-only `SourceDocument` (`source_doc_id`, `text`) to pass as the `document` input of the
    `contract_ingestion_pipeline` capability. The id should be a canonical, delimiter-safe source-doc id."""
    from rag_wright.capabilities.document_parse import SourceDocument

    return SourceDocument(source_doc_id=document_id, text=text)


def parse_document(document_id: str, path: Any, *, cache_dir: Any, metadata: dict | None = None) -> Any:
    """Docling-parse the file at `path` ONCE (content-hash gated + cached under `cache_dir`) into a
    structure-bearing `SourceDocument` -- `.parsed` carries the `DoclingDocument` so the chunker's structural pass
    fires on real headings, and `.text` holds the flattened text. This is the PDF/DOCX/HTML/MD ingest entry point
    of the engine API; pass the result as the `document` input of `contract_ingestion_pipeline`. The docling parse
    blocks; use `aparse_document` on an event loop."""
    from rag_wright.capabilities.document_parse import parsed_source_document

    p = Path(path)
    return parsed_source_document(document_id, p.name, p.read_bytes(), cache_dir=cache_dir, metadata=metadata)


async def aparse_document(document_id: str, path: Any, *, cache_dir: Any, metadata: dict | None = None) -> Any:
    """The async, deadline-bounded twin of `parse_document` (ADR-0057): runs the docling parse off the event loop
    so a hand-built async ingest can parse a document into a structure-bearing `SourceDocument` without blocking."""
    from rag_wright.capabilities.document_parse import aparsed_source_document

    p = Path(path)
    return await aparsed_source_document(document_id, p.name, p.read_bytes(), cache_dir=cache_dir, metadata=metadata)
