# RuleWright handoff: engine issue 0029 resolved — `write_graph` is now idempotent on its own (re-ingest converges)

Date: 2026-09-09 · **Re:** engine-issue 0029 · on `origin/main` (commit `999c198`) · ADR-0092 · **No API change. Keep passing `already_ingested` — but correctness no longer depends on it. Also fixed a latent bug in the 0027 `add_affiliation_edges` backfill.**

---

## TL;DR

We took **option A (create-if-absent)**. `write_graph` now writes a `Mentions` edge only if absent on `(chunk, entity)` and a `Relationship` edge only if absent on `(source, target, relationship_type, chunk_id)`. Nodes stay UPSERT. So **re-ingesting the same document converges** instead of appending — no more silent graph inflation. It's still one atomic transaction. **Live-verified**: the same graph written 4× stays at `entities=2, mentions=2, relationships=1` (was `2 / 8 / 4` before).

## What changed

- **The write is idempotent by itself.** The guarantee no longer lives in the caller's `already_ingested` guard — the write converges even if the guard is omitted. Your fixture that turned 4 edges into 16 without the guard would now stay at 4.
- **`chunk_id` is part of the Relationship key.** Two genuinely distinct edges between the same two parties from *different* contracts (different provenance `chunk_id`) both survive — only a re-ingest of the *same* document (same `chunk_id`) is de-duplicated. Your exposure/coverage arithmetic keeps distinct-contract edges.
- **Within a single call, a duplicate edge is written once** (not just across calls), so convergence is total.

## Keep your guard — it's still the right thing

**Do not remove `already_ingested=contract_exists(...)`.** It short-circuits the *whole pipeline* (parse, chunk, extract), not just the write — it saves far more than the write does. The only thing that changed is that **correctness stops depending on it**: a retry after a partial failure (the legitimate re-ingest case) now converges instead of doubling.

## A bonus fix you should know about (affects the 0027 backfill)

While live-testing this, we found the edge existence check we ship — written as `WHERE out.entity_id = ...` — **projects NULL on ArcadeDB** and so never matched. That means the **`add_affiliation_edges` backfill from issue 0027 was not actually edge-idempotent when run live** (it would silently re-add `AFFILIATE_OF` edges on a re-run, despite being documented as create-if-absent). The hermetic tests couldn't catch it (they stub the query).

- **Fixed** in the same commit: all three edge-existence checks now use `outV()`/`inV()`, which correctly dereference the endpoint. `add_affiliation_edges` now converges live (`added=1`, then `0, 0` on re-runs) — verified.
- **If you ran `scripts/backfill_affiliations.py` more than once against the same live DB before this fix**, you may have duplicate `AFFILIATE_OF` edges. Re-running now is safe (converges) but won't remove pre-existing duplicates. If you suspect an inflated store, count `AFFILIATE_OF` per `chunk_id` — any `chunk_id` with more than one identical edge is a pre-fix duplicate.

## What did NOT change

- No query capability, no API signature, no report shape changed.
- Nodes are still UPSERT (identity/resolution preserved on re-ingest).
- The write is still one atomic `execute_transaction` (all-or-nothing).

## Suggested confirmation on your side

Double-ingest one fixture corpus **without** your guard and assert the edge counts are identical after the 2nd, 3rd, 4th pass (that's the check that would have caught the original inflation). We verified this on a scratch DB; a product-side confirmation on your ingestion path closes it.

Reference: ADR-0092, `store/arcadedb.py` (`write_graph`, `_relationship_edge_exists`, `_mentions_edge_exists`, `add_affiliation_edges`), `tests/store/test_write_graph_idempotent.py`.
