"""EP-API-3 (ADR-0117): engine id/format accessors.

So a product never parses engine id strings or storage formats itself (today the seam reimplements these, drifting
from the engine). Heavy deps are imported lazily so `import rag_wright.api` stays light (progressive loading)."""
from __future__ import annotations

from typing import Optional


def document_of(entity_id: str) -> str:
    """The source-document id embedded in a span/chunk/clause id (`<source_doc_id>:<idx>:<hash>` -> the first,
    delimiter-safe segment). Empty in -> empty out."""
    from rag_wright.store.arcadedb import _doc_id_of

    return _doc_id_of(entity_id)


def id_source(requirement_id: str) -> str:
    """The source/policy of a Requirement id (`<source>:<section>:<hash>` -> the first segment; `source` is
    delimiter-safe via `canonical_source_doc_id`, so this is the same first-segment rule as `document_of`)."""
    from rag_wright.store.arcadedb import _doc_id_of

    return _doc_id_of(requirement_id)


def decode_bbox(raw: Optional[str]) -> Optional[tuple]:
    """Decode the engine's best-effort bounding box (stored as a JSON `[l,t,r,b]` string) to a `(l, t, r, b)` tuple,
    or None. The single canonical decoder (retires the product seam's copy)."""
    from rag_wright.capabilities.highlight_serve import _decode_bbox

    return _decode_bbox(raw)
