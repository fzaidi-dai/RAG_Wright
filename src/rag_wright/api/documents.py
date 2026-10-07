"""EP-API-2b / EP-API-6 (ADR-0117): building a document to feed the ingestion capability.

`source_document(document_id, text=...)` makes an engine `SourceDocument` from plain text (the simplest path, no
parser needed). `parse_document` / `aparse_document` make a STRUCTURE-BEARING `SourceDocument` from a file PATH via
the engine's real docling parse (`.parsed` set), so the full ingestion pipeline incl docling runs through the API --
not only the text fallback. Heavy deps imported lazily so `import rag_wright.api` stays light."""
from __future__ import annotations

from pathlib import Path
from typing import Any


def source_document(document_id: str, *, text: str) -> Any:
    """A text-only `SourceDocument` (`source_doc_id`, `text`) to pass as the `document` input of an ingestion
    capability that takes one (the reference pack's `contract_ingestion_pipeline`). `build_ingestion` reads files
    itself and does not take a `SourceDocument`. The id should be a canonical, delimiter-safe source-doc id."""
    from rag_wright.capabilities.document_parse import SourceDocument

    return SourceDocument(source_doc_id=document_id, text=text)


def parse_document(document_id: str, path: Any, *, cache_dir: Any, metadata: dict | None = None,
                   include_hidden_sheets: bool = True, tuning: Any = None) -> Any:
    """Docling-parse the file at `path` ONCE (content-hash gated + cached under `cache_dir`) into a
    structure-bearing `SourceDocument` -- `.parsed` carries the `DoclingDocument` so the chunker's structural pass
    fires on real headings, and `.text` holds the flattened text. Pass the result as the `document` input of an
    ingestion capability that takes one (the reference pack's `contract_ingestion_pipeline`), or to `table_rows`;
    `build_ingestion` parses its source paths itself through the same cached parse. The docling parse
    blocks; use `aparse_document` on an event loop. A spreadsheet's hidden sheets are ingested unless
    `include_hidden_sheets=False` (then listed in `.skipped_hidden_sheets`). Files embedded in an Office package are
    extracted as `.embedded` children linked to their records (`tuning.identifier` sets the identifier rule)."""
    from rag_wright.capabilities.document_parse import parsed_source_document

    p = Path(path)
    return parsed_source_document(document_id, p.name, p.read_bytes(), cache_dir=cache_dir, metadata=metadata,
                                  include_hidden_sheets=include_hidden_sheets, tuning=tuning)


async def aparse_document(document_id: str, path: Any, *, cache_dir: Any, metadata: dict | None = None,
                          include_hidden_sheets: bool = True, tuning: Any = None) -> Any:
    """The async, deadline-bounded twin of `parse_document` (ADR-0057): runs the docling parse off the event loop
    so a hand-built async ingest can parse a document into a structure-bearing `SourceDocument` without blocking."""
    from rag_wright.capabilities.document_parse import aparsed_source_document

    p = Path(path)
    return await aparsed_source_document(document_id, p.name, p.read_bytes(), cache_dir=cache_dir, metadata=metadata,
                                         include_hidden_sheets=include_hidden_sheets, tuning=tuning)
