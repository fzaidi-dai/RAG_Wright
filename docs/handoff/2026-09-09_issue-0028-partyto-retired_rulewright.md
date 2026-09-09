# RuleWright handoff: engine issue 0028 resolved — `PartyTo` retired (dead weight, superseded by `CONTRACTS_WITH` provenance)

Date: 2026-09-09 · **Re:** engine-issue 0028 · on `origin/main` (commit `a2df905`) · ADR-0091 · **No query-API change. One ingest-report field deprecated (still present, always 0). Action needed only if your product imports `corpus_party_link_fn`.**

---

## TL;DR

We took **option A (full retire)**. `PartyTo` (Entity → Contract, KG-7/ADR-0036) was written on every ingest and declared in the ontology, but **nothing read it** — and you confirmed you don't either (you attribute a relationship to its contract via the `CONTRACTS_WITH` edge's provenance `chunk_id`). It's now removed: the write, the `party_clause_linking` capability, the ontology declaration, and the dead scripts are gone. **Party→clause is unaffected** — it was never actually served by `PartyTo`.

## What changed

- **`PartyTo` edge is no longer written** on ingest (one less edge per contract, no write amplification).
- **`party_clause_linking` capability retired**: removed from the capability registry (`CANONICAL_CAPABILITY_SLUGS`) and its ARD manifest; the module, its test, and `scripts/link_party_clause.py` / `scripts/adoption_query_validate.py` are deleted.
- **Ontology**: `cbr:PartyToEdgeDecl` removed from `contract_bridge.ttl` — the `.ttl` now honestly reflects the edges the engine actually produces (ADR-0066).
- **Pipeline seam kept, provider removed**: `arun_corpus_ingestion(link_fn=...)` still accepts a generic no-op post-ingest hook, but the `corpus_party_link_fn` provider is deleted.

## Party → clause: how it's reached now (unchanged for you)

It was **never** served by `PartyTo`. It's reached exactly as you already do it:
- the **contract-scoped clause KG** (`contract_kg_serve`, keyed by `contract_id`) gives you the clauses of a contract, and
- **`CONTRACTS_WITH` provenance** (`chunk_id` on the edge) attributes parties to that contract.

No traversal, no capability, and no query surface changed. Nothing you call is affected.

## The one thing to check on your side

**`IngestionReport.party_links` is now a deprecated always-`0` field.** It's still there (report shape unchanged this cycle) so nothing breaks if you read it — but it will always be `0`, and a later ADR may remove it. Stop depending on it as a signal.

**If your product imports `corpus_party_link_fn`** (or passes it as `link_fn=` into `arun_corpus_ingestion`) — that symbol is **gone**. Drop the import and the `link_fn=` argument; the default no-op is what you want. If you never imported it (the engine-side CUAD path and dev scripts did, and are already updated), there's nothing to do.

## Existing KGs — no migration needed

`PartyTo` edges already sitting in KGs you've ingested are now **inert** — nothing traverses them. You can drop them lazily or leave them; they cost nothing at query time. **No re-ingest is required.**

## Note

The docling-graph clause template still has an internal `edge("PARTY_TO", ...)` **extraction annotation** (`dg_extraction.py`) — that is a different thing (a within-template annotation, not the store edge) and is intentionally untouched.

Reference: ADR-0091, `store/arcadedb.py`, `subgraphs/contract_ingestion_pipeline.py`, `capabilities/registry.py`, `contract_bridge.ttl`.
