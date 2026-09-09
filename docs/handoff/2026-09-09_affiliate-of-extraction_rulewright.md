# RuleWright handoff: engine issue 0027 resolved — AFFILIATE_OF now extracted (+ a backfill so you needn't re-ingest)

Date: 2026-09-09 · **Re:** engine-issue 0027 · on `origin/main` (the AFFILIATE_OF arc) · ADR-0090 · **No query-API change; the graph gains a new edge type in use**

---

## TL;DR

Ingestion now emits `AFFILIATE_OF` edges when a contract states a corporate affiliation, so `graph_query(..., relationship_type=AFFILIATE_OF)` has edges to traverse. Entities are **not** merged (the affiliate stays a separate node, as you required) — only the edge is added. It's **on by default** and costs ~nothing on contracts that state no affiliation (a lexical pre-filter). For KGs you already ingested, a **backfill script** adds the edges without re-ingesting.

## What the engine now does

- Per contract, from the preamble (one extra model call per contract, not per chunk), it looks for affiliation statements. A **lexical pre-filter** (`affiliate`, `subsidiary`, `parent`, `wholly-owned`, `under common control`, `a division of`, `owned by`) means a contract with no such language makes **no LLM call**.
- On a hit, it extracts `(organization, affiliate_of)` pairs and emits an `AFFILIATE_OF` edge plus an `ORGANIZATION` node for each side (so both endpoints resolve — the affiliate is a node even if it is not a signing party anywhere).
- **No entity merging.** `Acme Holdings Ltd` and `Acme Corp` stay separate nodes; only the edge between them is new.
- Toggle: `RAG_INGEST_AFFILIATIONS=0` disables it entirely.

Live-verified on your exact example: "Acme Holdings Ltd, an affiliate of Acme Corp" → `AFFILIATE_OF(Acme Holdings Ltd → Acme Corp)`; a plain two-party contract → no affiliation, no LLM call.

## Do you have to re-ingest? Only to gain the edges, and there's a shortcut

Affiliation edges exist only for contracts processed after this change, so a KG you already built has none until re-processed. But:

- **Only affiliation-bearing contracts change** — re-processing a plain contract adds nothing.
- **Two paths:**
  1. **Fresh ingests** — nothing to do; the edges come for free.
  2. **An existing KG you want to keep** — run the **edge-only backfill**, no re-ingest:
     ```
     uv run python scripts/backfill_affiliations.py --cache-dir <the ingest cache dir> --database <db> [--vset <verified_set.json>]
     ```
     It reuses the ingestion cache, extracts affiliations (pre-filtered), and writes **only** the `AFFILIATE_OF` edges + any missing affiliate node — it **never re-writes or clobbers** existing nodes/edges (`store.add_affiliation_edges`: create-node-if-absent, create-edge-if-absent). **Idempotent** — a contract already recorded in the affiliation cache is skipped, so re-running is a no-op, and the cache entry is written only after a successful write (an interrupted run is safely retried). It streams `X/N` progress. **Run it against a copy of the DB first if you want to inspect the writes.**

## Storage / query — already ready

No change was needed on the write or query sides: `write_graph` sets `relationship_type` generically and `graph_query` traverses any `RelationshipType`. The only thing missing was extraction, which is what this adds.

## One related issue filed for your side

`PartyTo` (Entity → Contract, KG-7/ADR-0036) is written + declared but **read nowhere** — engine issue **0028** (in this tracker) asks whether it's intended-load-bearing (name the reader) or dead weight (retire). You noted you don't need it (`CONTRACTS_WITH` provenance carries the contract). Not blocking; a decision to make.

Reference: ADR-0090, `capabilities/graph_extraction.py`, `subgraphs/contract_ingestion_pipeline.py`, `scripts/backfill_affiliations.py`.
