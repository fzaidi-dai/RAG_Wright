"""EP-API-2b (ADR-0117): building a document to feed the ingestion capability.

`source_document(document_id, text=...)` makes an engine `SourceDocument` from plain text (the simplest path, no
parser needed). A bytes/parse path (docling -> structure-bearing `SourceDocument`) is exposed as the ingestion
capabilities round out. Heavy deps imported lazily so `import rag_wright.api` stays light."""
from __future__ import annotations

from typing import Any


def source_document(document_id: str, *, text: str) -> Any:
    """A text-only `SourceDocument` (`source_doc_id`, `text`) to pass as the `document` input of the
    `contract_ingestion_pipeline` capability. The id should be a canonical, delimiter-safe source-doc id."""
    from rag_wright.subgraphs.contract_ingestion_pipeline import SourceDocument

    return SourceDocument(source_doc_id=document_id, text=text)
