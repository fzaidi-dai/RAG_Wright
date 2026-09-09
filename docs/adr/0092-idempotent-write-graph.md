# ADR-0092: `write_graph` is idempotent on its own (create-if-absent), and endpoint filters use `outV()`/`inV()`

**Status:** accepted · **Date:** 2026-09-09 · **Issue:** engine 0029 (RuleWright) · **Related:** ADR-0090 (AFFILIATE_OF + `add_affiliation_edges`), FR-S.1 (one store, atomic graph write)

## Context

`store.write_graph` UPSERTs nodes but **created edges unconditionally**, so re-ingesting the same document appended a second, byte-identical set of `Mentions` and `Relationship` edges (same endpoints, same `relationship_type`, same provenance `chunk_id`). Nothing errored; the graph silently inflated, and every edge count taken from it inflated with it. RuleWright (issue 0029) hit this at T-4.6: a four-document corpus ingested four times became `Contracts With: 16, Affiliate Of: 4`.

Production was safe only because every caller passes `already_ingested=contract_exists(...)`, which short-circuits the whole pipeline before the write. But the guarantee lived in the wrong place — in each caller remembering a keyword argument — and it had already failed silently once (a green test run reporting on a 4× inflated graph). The ask: make the write converge on its own, without removing the caller-side short-circuit.

A second defect surfaced while live-testing the fix: an edge existence check written as `WHERE out.entity_id = ...` **projects NULL on this ArcadeDB** (the bare `out`/`in` link is not dereferenced in a filter), so it never matched and never deduplicated. `add_affiliation_edges` (ADR-0090's backfill) used exactly this shape and was therefore **not actually edge-idempotent live** either — a latent bug the hermetic (substring-stubbed) tests could not catch.

## Decision

Make `write_graph` create-if-absent (issue 0029 option A), and reach endpoint properties with `outV()`/`inV()`.

- **Nodes stay UPSERT** (already idempotent — a re-ingest converges the node, never duplicates it).
- **A `Mentions` edge is created only if absent** on `(chunk, entity)`.
- **A `Relationship` edge is created only if absent** on the **full provenance key `(source, target, relationship_type, chunk_id)`** — including `chunk_id`, so two genuinely distinct edges between the same parties from *different* contracts (different provenance) both survive, while a re-ingest of the *same* document adds nothing.
- **Still one atomic transaction:** existence is checked with read-only pre-queries *before* the transaction is built; the transaction then contains the node UPSERTs plus only the absent-edge CREATEs and is applied via `execute_transaction` (all-or-nothing, unchanged).
- **Within-batch de-dup too:** a `seen` set drops a duplicate edge appearing twice in a single call, so convergence is total, not just cross-call.
- **Endpoint filters use `outV()`/`inV()`** (`outV().entity_id`, `inV().entity_id`, `outV().chunk_id`) in `_relationship_edge_exists`, `_mentions_edge_exists`, **and** `add_affiliation_edges` — a bare `out.entity_id` projects NULL here (verified live) and silently defeats the check.

## Consequences

- **Re-ingesting a document converges.** Live-verified on a scratch DB: the same graph written four times stays at `entities=2, mentions=2, relationships=1` (was 2 / 8 / 4 before the fix). `add_affiliation_edges` likewise converges live (`added=1` then `0, 0`); before the `outV()`/`inV()` fix it silently appended on every re-run.
- **Correctness no longer depends on `already_ingested`.** That guard stays a useful whole-pipeline short-circuit (it saves parse/chunk/extract, not just the write), and callers keep passing it; but the write is now correct without it. A retry after a partial failure — the exact case where a re-ingest is legitimate — now converges instead of doubling.
- **Cost:** two read-only existence pre-queries per edge before the transaction. Negligible against extraction, and only on the write path (the pipeline still short-circuits earlier when the caller guards).
- **No migration for the append behaviour**, but a KG previously ingested more than once through an unguarded path already holds duplicate edges; those are not removed by this change (it prevents *new* duplication). A de-dup pass can be run separately if a store is known to be inflated.
- **Hermetic tests need a live anchor.** The substring-stubbed tests could not have caught the `out.entity_id`→NULL defect; it was caught only by a live double-ingest on the running ArcadeDB. The live convergence check is the gate for this change, and the corrected `outV()`/`inV()` shape is now the one pattern used by all three edge-existence checks.
