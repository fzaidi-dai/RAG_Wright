# ADR-0142: The default record writer keeps one edge per type and endpoints

**Status:** accepted · **Date:** 2026-11-07 · **Related:** PS-63, engine gap G8, TexWright's request
`2026-10-15-g8-duplicates-edges-within-one-ingest.md`, ADR-0124 (ING-4b `kg_ensure_edges`), issue 0029
(`outV()`/`inV()` endpoint filters)

## Context

With no `writer=`, `build_ingestion` wrote records with `kg_write`: nodes upserted by key, edges created with a plain
`CREATE EDGE`. The engine's own guidance has an entity several units mention emitted by its key from each unit, so
each such unit also emitted the edge to it, and that edge landed once per unit, within one ingest and again on every
re-ingest. Node counts stayed right, so only a traversal showed it (TexWright: 3 children read back as 6). The
store's idempotent `kg_ensure_edges` runs a query and a write per edge outside any transaction, and scans the edge
type for each edge.

## Decision

- `Store.kg_write` takes `ensure_edges` (default `False`). When true, an edge is identified by its type and its two
  endpoints (direction counts): the batch keeps each edge's last props; each edge is an index-driven `UPDATE` of an
  existing one, reached through `outE` from its indexed from node, then `CREATE EDGE ... IF NOT EXISTS`, all in the
  write's one transaction under the same conflict retry. ArcadeDB 26.7 was probed live: `IF NOT EXISTS` holds within
  one transaction, across transactions and across concurrent writers.
- The ingestion default writer passes `True`; `rag_wright.api.kg_write` exposes the flag for a domain's own writer.
- Without the flag `kg_write` is unchanged: a caller whose parallel edges each carry their own provenance (the
  reference pack's clause-to-value assertions, RuleWright's writer) keeps them.

## Consequences

- An edge several units emit, or a re-ingest writes again, lands once with the last unit's props; G8 is closed.
- A domain that means parallel edges of one type between the same two nodes, and uses the default writer, now gets
  one; it passes a `writer=` that calls `kg_write` without the flag.
- A re-ingest still does not remove records a document no longer yields. `kg_ensure_edges` is unchanged.
