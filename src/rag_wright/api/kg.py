"""EP-API-3 (ADR-0117): scoped KG access over the opaque workspace handle.

Thin wrappers that delegate to the handle's resolved store, so a product does generic typed-KG reads/writes (and
span-position lookups for citations) WITHOUT importing `ArcadeDBStore` or touching `ws._store`. The generic
`kg_read`/`kg_write` primitives already live on the store (DD-1a/b); this exposes them on the API."""
from __future__ import annotations

from typing import Any, Optional

from rag_wright.api.workspace import WorkspaceHandle

# the span-position fields a citation needs (NOT the dense vector) -- offsets + page/bbox provenance
_SPAN_POSITION_FIELDS = [
    "span_id", "parent_chunk_id", "span_index", "text", "primary_tag", "document_id",
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


def kg_edges(ws: WorkspaceHandle, from_type: Optional[str] = None, *, where: Optional[dict] = None,
             key_range: Optional[tuple] = None, direction: str = "out", edge_type: Optional[str] = None,
             edge_where: Optional[dict] = None, target_where: Optional[dict] = None,
             select: dict) -> list[dict]:
    """Generic edge TRAVERSAL over the workspace (see `Store.kg_edges`): node-start out/in MATCH (by `where`
    equality/membership or a contract-scope `key_range`) or a direct edge scan; `select` projects `c.`/`e.`/`v.`
    expressions. The engine's relational/graph primitive on the API, so a domain's graph query never touches
    `ws._store`. (`NOT_NULL` for a presence filter is `rag_wright.store.seam.NOT_NULL`.)"""
    return ws._store.kg_edges(from_type, where=where, key_range=key_range, direction=direction,
                              edge_type=edge_type, edge_where=edge_where, target_where=target_where, select=select)


def entities_by_name(ws: WorkspaceHandle, name: str) -> list[dict]:
    """Resolve an entity NAME to every entity node it matches: `[{entity_id, name, entity_type}]` (the engine owns
    the surface-form normalization, so variants collapse to one id). One name can match several nodes (a resolved
    node + an unlinked ref sharing a clustering key) -- all are returned. `entity_id` is exactly the
    `start_entity_id` a graph traversal takes. The engine's generic entity-lookup primitive on the API."""
    return ws._store.entities_by_name(name)


def span_positions(ws: WorkspaceHandle, document: str) -> list[dict]:
    """Every span of `document` with its position provenance (doc offsets, pages, DECODED bbox), ordered by document
    position. The engine MECHANISM behind a product's citation/highlight types -- the product wraps these rows into
    its own presentation type (e.g. `SpanLocation`)."""
    from rag_wright.api.ids import decode_bbox
    from rag_wright.store.arcadedb import SPAN_TYPE

    rows = ws._store.kg_read(SPAN_TYPE, fields=_SPAN_POSITION_FIELDS, where={"document_id": document},
                             order_by="doc_start")
    return [{**r, "bbox": decode_bbox(r.get("bbox"))} for r in rows]
