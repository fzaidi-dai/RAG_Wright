# RuleWright handoff: engine issue 0031 resolved — a `documents` scope on corpus retrieval + graph traversal

Date: 2026-09-10 · **Re:** engine-issue 0031 · on `origin/main` (commit `617ae1d`) · ADR-0094 · **New optional arg on two entrypoints. Span scoping needs nothing from you; graph scoping needs a one-time backfill of existing KGs.**

---

## TL;DR

Both corpus surfaces now take a store-side `documents` scope, exactly the shape you asked for and mirroring compliance `sources` (issue 0007):

```python
production_typed_property_retrieval(store=..., embedder=..., extract_model=..., k=8,
                                    judge_model_id=..., documents=["<doc_id>", ...])   # span sweeps
graph_query(start_entity_id, store=..., relationship_type=..., max_hops=1,
            documents=["<doc_id>", ...])                                              # counterparty exposure
```

- `documents=None` → whole database (today's behavior, unchanged).
- A list → the search/traversal runs **only** over those documents, filtered **in the store** — out-of-scope spans are never pooled, embedded, reranked, or judged; out-of-scope edges are never traversed.
- The ids are **your own content-hash `document_id`s** — the same ones that prefix every `span_id`/`chunk_id`. No translation.
- **An unknown id raises `UnknownDocumentError`** (`rag_wright.capabilities.document_scope`), not a silent empty — a filter that quietly matches nothing is indistinguishable from an empty matter, which is what this exists to prevent. `documents=[]` is a valid scope-to-nothing (empty result, no query).

Live-verified end to end: span search scoped to one doc returns only that doc's spans; a traversal from a party scoped to a *different* matter returns empty (proving an edge in another matter is never reached, not just filtered after); unknown id raises.

## What you must do

**Span retrieval — nothing.** `Span` already carried `contract_id` (the source-document id), so scoping works on every existing KG with no migration.

**Graph traversal — run one backfill per existing KG.** Graph scoping filters each edge on a new `source_doc_id` property (derived from the edge's provenance `chunk_id`). Fresh ingests write it automatically, but edges created before this change don't have it, so a scoped `graph_query` would match none of them until you backfill:

```
uv run python scripts/backfill_edge_source_doc_id.py --database <db>
```

It stamps `source_doc_id` on existing `Relationship` edges in place (never touches endpoints, `relationship_type`, `chunk_id`, or confidence), is **idempotent** (`WHERE source_doc_id IS NULL`), and streams `X/N`. Run it against a copy first if you want to inspect. Until you backfill a given KG, keep graph queries unscoped on it (or you'll get empty results).

## How the scope is enforced (so you can trust the boundary)

- **Span leg:** `Span.contract_id IN [your docs]`, applied to the fused hybrid-search result. When scoped, the vector legs pull a **larger candidate pool** (1000) before the cut, because the KNN ranks across the whole index and only then is scoped — this keeps a small workspace from under-filling `k`.
  - *One honest caveat:* the vector index still ranks globally and is filtered after (ArcadeDB has no vector pre-filter yet; ADR-0008). For ordinary workspaces (tens–hundreds of docs) the 1000-pool comfortably holds the in-scope top-k. If you ever have a very large workspace whose in-scope spans could rank beyond 1000 globally and see misses, tell us and we'll raise the pool or revisit. The graph leg has **no** such caveat — its filter is exact.
- **Graph leg:** `source_doc_id IN [your docs]` on **every** edge of a path (both hops of a 2-hop), so a traversal cannot route *through* an out-of-scope contract to reach an in-scope target. This is why post-filtering the result was not acceptable and we didn't do it.

## Validation source

`UnknownDocumentError` is raised when a `documents` id is present in **neither** the span index nor the graph edges of the store (`store.known_document_ids()` = union of distinct `Span.contract_id` and `Relationship.source_doc_id`). So a doc that has spans but no extracted relationships (or vice-versa) is still a valid scope.

## Not done (by design, tell us if you need it)

The scope is on the in-process production entrypoints you compose, not on the MCP tools (`typed_property_retrieval_mcp` etc.). If you need a scoped MCP surface, say so and we'll add the arg there too.

Reference: ADR-0094, `store/arcadedb.py` (`span_hybrid_search`, `graph_neighbors`, `known_document_ids`, `_doc_id_of`), `capabilities/document_scope.py`, `subgraphs/typed_property_retrieval.py`, `capabilities/graph_query.py`, `scripts/backfill_edge_source_doc_id.py`.
