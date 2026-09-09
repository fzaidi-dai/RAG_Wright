# ADR-0091: retire the `PartyTo` (Entity → Contract) edge and its `party_clause_linking` capability

**Status:** accepted · **Date:** 2026-09-09 · **Issue:** engine 0028 (RuleWright) · **Supersedes (in part):** KG-7 / ADR-0036 (the Party↔Contract unifying link) · **Related:** ADR-0090 (AFFILIATE_OF extraction), ADR-0033 (unified contract KG)

## Context

`PartyTo` — an `Entity → Contract` edge (KG-7 / ADR-0036) — was **written on every ingest** by the `party_clause_linking` capability and **declared** in the ontology (`contract_bridge.ttl`) and the capability registry, but **nothing in the engine reads it**: there is no `SELECT`/`MATCH`/traversal over `PartyTo` anywhere in the source, and the intended "party → its clauses via `PartyTo`" reader was never built. RuleWright (issue 0028) confirmed it does not consume the edge either — it attributes a relationship to a contract via the `CONTRACTS_WITH` edge's provenance `chunk_id` (which carries the source contract id), not via `PartyTo`.

So the edge was produced and stored on every ingest and consumed by no one: pure write amplification plus schema surface a future reader could mistake for load-bearing (it is declared and populated, so it *looks* supported), and a capability + tests + scripts maintained for a path with no consumer.

The two failure modes issue 0028 named were (1) load-bearing-but-unwired or (2) dead weight superseded by `CONTRACTS_WITH` provenance. This is (2): party→clause is fully reachable today without `PartyTo` — the contract-scoped clause KG (`contract_kg_serve`, keyed by `contract_id`) plus `CONTRACTS_WITH` provenance answer "which parties, which clauses, in which contract" without a dedicated Entity→Contract edge.

## Decision

Retire `PartyTo` and the `party_clause_linking` capability entirely (issue 0028 option A, full retire).

- **Store:** remove `write_party_contract_links` and the `PARTY_TO_EDGE_TYPE = "PartyTo"` constant from `store/arcadedb.py`; drop `PartyTo` from the `ensure_schema` structural-edge list. Also removed `all_entities` (the party-only helper that existed only to feed the `PartyTo` link reader; no other caller). `all_contracts` is **kept** — it feeds `scripts/backfill_clause_span_id.py`, a corpus-wide backfill, and is re-documented neutrally.
- **Capability:** delete `capabilities/party_clause_linking.py`, its test, and the `link_party_clause.py` / `adoption_query_validate.py` scripts; remove the `party_clause_linking` slug from `CANONICAL_CAPABILITY_SLUGS` and its `CapabilityManifest`.
- **Ontology:** remove `cbr:PartyToEdgeDecl a cbr:KgStructuralEdge` from `contract_bridge.ttl` and drop `PartyTo` from the structural-edge header comment. Per ADR-0066 the edge type is ontology-declared knowledge; retiring the consumer means retiring the declaration too, so the `.ttl` stays the honest source of truth for what the engine actually produces.
- **Pipeline seam kept, provider removed:** `arun_corpus_ingestion(link_fn=...)` keeps its generic no-op `LinkFn` seam (a corpus-level post-ingest hook returning an int count), but the `corpus_party_link_fn` provider is deleted. `IngestionReport.party_links` is **kept as a deprecated always-`0`** field (removal is a report-shape change deferred to avoid breaking a consumer reading the field this cycle).
- **Callers updated:** `corpus/cuad_ingestion.py` and the dev ingest scripts drop the `link_fn=corpus_party_link_fn` / `PARTY_TO_EDGE_TYPE` references; the `corpus_ingest` SKILL and affected docstrings are reworded.

## Consequences

- One fewer edge written per ingest (write-amplification gone) and one fewer declared-but-unread edge type on the schema surface — no reader can mistake `PartyTo` for a supported traversal, because it no longer exists.
- **Party→clause is unaffected**: it was never actually served by `PartyTo`. It is reached via the contract-scoped clause KG + `CONTRACTS_WITH` provenance `chunk_id`, exactly as RuleWright already does. No query capability changed.
- The `link_fn` seam survives, so a genuine future corpus-level post-ingest step (a real reader-backed link, an audit pass) has a wired-in place to attach without re-plumbing the driver. `IngestionReport.party_links` stays `0` and is safe to ignore; a later ADR may drop it.
- Full suite green after retirement (1482 passed, 44 skipped). No data migration is needed: existing `PartyTo` edges in already-ingested KGs are simply orphaned and ignored; they can be dropped lazily or left inert (nothing traverses them).
- The docling-graph template's `edge("PARTY_TO", ...)` annotation (`dg_extraction.py`) is a **different thing** (an in-template extraction annotation, not the store edge) and is intentionally left untouched.
