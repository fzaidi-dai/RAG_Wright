"""EP-API-3 (ADR-0117): scoped KG access over the opaque workspace handle.

Thin wrappers that delegate to the handle's resolved store, so a product does generic typed-KG reads/writes (and
span-position lookups for citations) WITHOUT importing `ArcadeDBStore` or touching `ws._store`. The generic
`kg_read`/`kg_write` primitives already live on the store (DD-1a/b); this exposes them on the API.

**Names and values (PS-53).** Filter VALUES are always escaped, so they may come from a request. Every NAME -- a type,
a `where` / `key_range` / `set` / `fields` / `distinct` field, an `order_by` term, a `select` alias or expression,
a `KgNode` / `KgEdge` property -- is checked before any SQL is built: an identifier (letters, digits, underscores;
`@rid` / `@type` / `@in` / `@out` where a field is read), `order_by` as `field [ASC|DESC]` terms, a `kg_edges`
select as a field optionally on `c.` / `e.` / `v.` / `inV().` / `outV().`. Anything else raises `ValueError` and
nothing is sent, so a request-chosen field name cannot change the query. It CAN still name a field no record has:
the store is schema-flexible (undeclared properties are stored and read, the engine's own types carry some), so an
unknown field matches no rows rather than raising. A product that exposes a field choice checks it against the
fields it means to offer."""
from __future__ import annotations

from typing import Any, Optional

from rag_wright.api.workspace import WorkspaceHandle

# the span-position fields a citation needs (NOT the dense vector) -- offsets + page/bbox provenance
_SPAN_POSITION_FIELDS = [
    "span_id", "parent_chunk_id", "span_index", "text", "primary_tag", "document_id",
    "doc_start", "doc_end", "pages", "bbox",
]


def kg_read(ws: WorkspaceHandle, node_type: str, *, where: Optional[dict] = None, fields: Optional[list] = None,
            distinct: Optional[str] = None, order_by: Optional[str] = None, limit: Optional[int] = None,
            key_range: Optional[tuple] = None) -> list[dict]:
    """Read typed nodes of `node_type` from the workspace (see `Store.kg_read`). Each `where` value is a scalar
    (equality), a list (membership; an empty list is scope-to-nothing -> `[]`), `NOT_NULL` (presence), a `Range`
    (comparisons, on any number of fields: `{"weight": Range(ge=180, le=220), "tensile": Range(gt=400)}`) or a
    `Contains` (a literal substring, case-insensitive unless `case_sensitive=True`; an empty field never matches:
    `{"composition": Contains("UHMWPE")}`); all
    filters are AND-ed and evaluated in the store. `key_range=(field, lo, hi)` is the half-open shorthand
    (`lo <= field < hi`). Then projection, distinct, order and limit. Every name is checked, never interpolated
    unchecked (the module docstring): a request-chosen field is safe, but an unknown one matches nothing."""
    return ws._store.kg_read(node_type, where=where, fields=fields, distinct=distinct, order_by=order_by, limit=limit,
                             key_range=key_range)


def kg_count(ws: WorkspaceHandle, type_name: str, *, where: Optional[dict] = None,
             key_range: Optional[tuple] = None) -> int:
    """The number of vertices or edges of `type_name` matching `where` / `key_range` (see `Store.kg_count`); `where`
    as in `kg_read`, `Range` and `Contains` values included."""
    return ws._store.kg_count(type_name, where=where, key_range=key_range)


def kg_delete(ws: WorkspaceHandle, type_name: str, *, where: Optional[dict] = None,
              key_range: Optional[tuple] = None) -> int:
    """Delete the matching vertices (with their edges) or edges of `type_name`; all of them when `where` and
    `key_range` are both None. Returns the number deleted (see `Store.kg_delete`)."""
    return ws._store.kg_delete(type_name, where=where, key_range=key_range)


def kg_update(ws: WorkspaceHandle, type_name: str, *, set: dict, where: Optional[dict] = None,
              key_range: Optional[tuple] = None) -> int:
    """Set fields on the matching vertices or edges of `type_name`, changing only rows where a set field differs;
    returns the number changed (see `Store.kg_update`)."""
    return ws._store.kg_update(type_name, set=set, where=where, key_range=key_range)


def kg_write(ws: WorkspaceHandle, nodes: list, edges: Any = ()) -> None:
    """Upsert typed `nodes` + create typed `edges` in one transaction (see `Store.kg_write`). `nodes`/`edges` are
    `KgNode`/`KgEdge` (from `rag_wright.api`); the store encodes each field per its pack-declared type. A node type
    the workspace's schema does not declare fails the write."""
    ws._store.kg_write(nodes, edges)


def kg_edges(ws: WorkspaceHandle, from_type: Optional[str] = None, *, where: Optional[dict] = None,
             key_range: Optional[tuple] = None, direction: str = "out", edge_type: Optional[str] = None,
             edge_where: Optional[dict] = None, target_where: Optional[dict] = None,
             select: dict) -> list[dict]:
    """Generic edge TRAVERSAL over the workspace (see `Store.kg_edges`): node-start out/in MATCH (by `where`
    equality/membership or an id-prefix `key_range`) or a direct edge scan; `select` projects `c.`/`e.`/`v.`
    expressions. The engine's relational/graph primitive on the API, so a domain's graph query never touches
    `ws._store`. (`NOT_NULL` from `rag_wright.api` is the presence filter.)"""
    return ws._store.kg_edges(from_type, where=where, key_range=key_range, direction=direction,
                              edge_type=edge_type, edge_where=edge_where, target_where=target_where, select=select)


def entities_by_name(ws: WorkspaceHandle, name: str) -> list[dict]:
    """Resolve an entity NAME to every entity node it matches: `[{entity_id, name, entity_type}]` (the engine owns
    the surface-form normalization, so variants collapse to one id). One name can match several nodes (a resolved
    node + an unlinked ref sharing a clustering key) -- all are returned. `entity_id` is exactly the
    `start_entity_id` a graph traversal takes. The engine's generic entity-lookup primitive on the API."""
    return ws._store.entities_by_name(name)


def span_positions(ws: WorkspaceHandle, document: str) -> list[dict]:
    """Every span of `document` with its position provenance, ordered by document position. Each row:
    `span_id`, `parent_chunk_id`, `span_index`, `text`, `primary_tag` (the span tagger's primary tag, "" if untagged),
    `document_id`, `doc_start` / `doc_end` (document-absolute offsets), `pages`, and `bbox` DECODED to a
    `(l, t, r, b)` tuple or None. The engine MECHANISM behind a product's citation/highlight types -- the product
    wraps these rows into its own presentation type (e.g. `SpanLocation`)."""
    from rag_wright.api.ids import decode_bbox
    from rag_wright.store.arcadedb import SPAN_TYPE

    rows = ws._store.kg_read(SPAN_TYPE, fields=_SPAN_POSITION_FIELDS, where={"document_id": document},
                             order_by="doc_start")
    return [{**r, "bbox": decode_bbox(r.get("bbox"))} for r in rows]


def span_search(ws: WorkspaceHandle, query: str, *, k: int = 10, documents: Optional[list] = None,
                tag: Optional[str] = None, dense_floor: int = 3) -> list:
    """Hybrid search over the workspace's span index (every ingested span, embedded dense and sparse): the two legs
    fused by Reciprocal Rank Fusion in the store, with the top `dense_floor` pure-dense spans guaranteed a place, so
    a strong semantic match a common-token query would push out of the fused pool is kept (0 turns it off). Returns
    up to `k` `SpanHit`s, best first, each citation-ready: `span_id`, `text`, `document_id`, `doc_start` /
    `doc_end`, `pages`, `bbox`, `primary_tag`, `rank`, and `found_by` (`"hybrid"` and/or `"dense"`). `documents`
    scopes it to those source documents in the store (`None` = all, `[]` = nothing); `tag` keeps one primary tag.
    No reranking: compose `BGEReranker` over the hits if you want one. Raises `RuntimeError` when the workspace
    has no embedder. Safe to call from several threads or requests at once: it shares the workspace's one embedder,
    which is thread-safe (PS-52)."""
    from rag_wright.capabilities.span_search import span_search as _search

    if ws.embedder is None:
        raise RuntimeError("span_search needs the workspace's embedder; this workspace was opened without one")
    return _search(query, store=ws._store, embedder=ws.embedder, k=k, documents=documents, tag=tag,
                   dense_floor=dense_floor)


async def aspan_search(ws: WorkspaceHandle, query: str, *, k: int = 10, documents: Optional[list] = None,
                       tag: Optional[str] = None, dense_floor: int = 3) -> list:
    """`span_search` off the event loop (the query embedding and the store calls block), for an async caller."""
    import asyncio

    return await asyncio.to_thread(span_search, ws, query, k=k, documents=documents, tag=tag,
                                   dense_floor=dense_floor)
