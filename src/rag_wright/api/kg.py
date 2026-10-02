"""EP-API-3 (ADR-0117): scoped KG access over the opaque workspace handle.

Thin wrappers that delegate to the handle's resolved store, so a product does generic typed-KG reads/writes (and
span-position lookups for citations) WITHOUT importing `ArcadeDBStore` or touching `ws._store`. The generic
`kg_read`/`kg_write` primitives already live on the store (DD-1a/b); this exposes them on the API."""
from __future__ import annotations

from typing import Any, Optional

from rag_wright.api.workspace import WorkspaceHandle

# the span-position fields a citation needs (NOT the dense vector) -- offsets + page/bbox provenance
_SPAN_POSITION_FIELDS = [
    "span_id", "parent_chunk_id", "span_index", "text", "function", "contract_id",
    "doc_start", "doc_end", "pages", "bbox",
]


def kg_read(ws: WorkspaceHandle, node_type: str, *, where: Optional[dict] = None, fields: Optional[list] = None,
            distinct: Optional[str] = None, order_by: Optional[str] = None, limit: Optional[int] = None) -> list[dict]:
    """Read typed nodes of `node_type` from the workspace (see `Store.kg_read`). Equality/`IN` filters, projection,
    distinct, order, limit; an empty list `where` value is scope-to-nothing -> `[]`."""
    return ws._store.kg_read(node_type, where=where, fields=fields, distinct=distinct, order_by=order_by, limit=limit)


def kg_write(ws: WorkspaceHandle, nodes: list, edges: Any = ()) -> None:
    """Upsert typed `nodes` + create typed `edges` in one transaction (see `Store.kg_write`). `nodes`/`edges` are
    `KgNode`/`KgEdge` (from `rag_wright.store.seam`); the store encodes each field per its pack-declared type."""
    ws._store.kg_write(nodes, edges)


def span_positions(ws: WorkspaceHandle, document: str) -> list[dict]:
    """Every span of `document` with its position provenance (doc offsets, pages, DECODED bbox), ordered by document
    position. The engine MECHANISM behind a product's citation/highlight types -- the product wraps these rows into
    its own presentation type (e.g. `SpanLocation`)."""
    from rag_wright.api.ids import decode_bbox
    from rag_wright.store.arcadedb import SPAN_TYPE

    rows = ws._store.kg_read(SPAN_TYPE, fields=_SPAN_POSITION_FIELDS, where={"contract_id": document},
                             order_by="doc_start")
    return [{**r, "bbox": decode_bbox(r.get("bbox"))} for r in rows]
