# ADR-0128: Two public tiers: `rag_wright.api` for products, `rag_wright.pack_sdk` for packs

**Status:** accepted · **Date:** 2026-10-09 · **Related:** ADR-0117 (the engine API layer), ADR-0124 (ingestion hooks),
ADR-0125 / ADR-0126 / ADR-0127 (decisions taken inside this workstream), gaps G15-G21

## Context

The RuleWright migration Q&A (2026-10-08) showed that a product following the engine's own reference seam still
reached engine internals: `ws._store` to build pack stores (G15), a module path to parse uploaded bytes (G16),
`models.profiles` for `ModelRole` (G17), `models.usage` / `models.tracing` to meter and trace (G18),
`capabilities.*` to generate answers and judge relevance (G19), a source-relative path for classifier weights (G20),
and a forked pack would depend on about 40 internal modules plus raw store SQL (G21). An audit inside the workstream
also found domain vocabulary in the generic engine (PS-R5).

## Decision

1. **Two declared public tiers, with one compatibility promise** (breaking changes only in a breaking release, with a
   migration note): `rag_wright.api` is what a product's seam uses; `rag_wright.pack_sdk` is what a domain pack's own
   code additionally uses (re-exports of the engine's objects, disjoint from `api`; an explicit form that would clash
   with the API's workspace form is renamed). Everything else is internal.
2. **The API gains** `pack_store(ws, cls)` (the store stays private), bytes parsing (`parse_document_bytes`,
   `IngestSource(data=, name=)`), `ModelRole`, `record_usage` / `traced_run` / `traced_step`, `agenerate_answer` /
   `ajudge_spans` with their types (models from the workspace's roles, domain `guidance=`), `kg_count` / `kg_delete` /
   `kg_update` / `kg_read(key_range=)`, `StoreConfig.from_env()`, and the ingestion hooks `unit_representative` (ADR-0126)
   and `chunk_discoverer` (`default_chunk_discoverer(guidance=)`). Classifier weights resolve under one models root,
   `RAG_MODELS_DIR`, and the reference weights are fetched from GCS with checksums.
3. **Enforcement:** an import-linter contract keeps `rag_wright.packs` on the two tiers (direct imports of any other
   engine package are forbidden); the engine's reference pack is the proof the tiers suffice. A vocabulary guard keeps
   domain terms out of the engine's skills, capability discovery text and both tiers' docstrings, with a ratchet on the
   rest of the generic engine.

## Consequences

- A product and a forked pack can build on declared surfaces; engine refactors behind them are free.
- Each new pack need becomes a deliberate SDK export (reviewed for neutrality by the guard), not an internal import.
- The generated references (`docs/api/README.md`, `docs/api/pack_sdk.md`) and the guards fail the build on drift.
- RuleWright's path: `docs/specs/public-surface/rulewright-migration-0.3.0.md`.
