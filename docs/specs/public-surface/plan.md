# Public surface for products and packs (PS-*)

Status ledger for the workstream that closes the public-API gaps the RuleWright migration exposed: engine gaps
**G15-G21** in `docs/domain-adaptation/_engine-gaps.md`. Each task runs the working loop (contract → red → green →
verify → approval gate → one commit). Target release: **0.3.0** (the exports are `feat:`; nothing existing is removed
except where a task says so).

**Why.** A product is meant to touch the engine only through `rag_wright.api`, and a domain pack (the engine's
reference pack today, a product's own pack next) only through a stable surface. Today a product that follows the
engine's own reference seam reaches `ws._store` (G15), parses uploaded bytes through an engine module (G16), imports
`ModelRole` to use `ws.model_id` (G17), meters and traces through `models.usage` / `models.tracing` (G18), generates
answers and judges relevance through `capabilities.*` modules (G19), cannot point the classifier fleet at its own
weights (G20), and a forked pack depends on about 30 engine-internal modules plus raw store SQL (G21).

**Target.** Two declared tiers, both enforced:

| tier | who imports it | what it holds |
|---|---|---|
| `rag_wright.api` | a product's seam | config, workspace, invokers, KG reads/writes, document parsing (path AND bytes), ingestion, usage + trace correlation, generation + relevance, pack loading |
| `rag_wright.pack_sdk` (new) | a domain pack's code (the reference pack and any product pack) | everything a pack builds on: the pack store base over the workspace store, the KG query primitives, the structured-output model seam, the LangGraph scaffold, the ingestion and extraction contracts, provenance and identifiers, concurrency helpers |

A pack may import `rag_wright.api` + `rag_wright.pack_sdk` (+ its own modules); an import contract enforces it on the
reference pack, which is the proof that the tier is sufficient. Everything else stays internal and may change.

## Design decisions (for review)

1. **G15, the store for pack store extensions.** A pack store wraps the workspace store through a public, typed
   route: `pack_store(ws, cls)` (in `rag_wright.api`) constructs `cls(<the workspace's store>)`, and the store is
   typed as the `Store` protocol. `ws._store` stays private; the reference seam and the guides switch to
   `pack_store(ws, ContractKGStore)`. (Alternative rejected: a public `ws.store` property, which hands products the
   whole store and invites them to bypass packs.)
2. **G21, the query primitives** that replace a pack's raw SQL (the 18 calls in the reference pack reduce to four):
   `kg_count(type, where=None)`, `kg_delete(type, where=None)` (vertex or edge type; `where` like `kg_read`),
   `kg_update(type, set={...}, where=...)` (with a not-equal guard for idempotent updates), and `key_range` on
   `kg_read` (it already exists on `kg_edges`). Added to the `Store` protocol and `ArcadeDBStore`; the reference pack
   then makes no `_query` / `_command` call.
3. **G16, bytes.** `parse_document_bytes(document_id, name, data, *, cache_dir, metadata=None, include_hidden_sheets=True,
   tuning=None)` and its async, deadline-bounded twin `aparse_document_bytes(...)` in `rag_wright.api` (the existing
   generic `capabilities.document_parse` functions). `IngestSource` gains a bytes form (`data` + `name` instead of
   `path`) so `build_ingestion` ingests uploads without temp files.
4. **G17.** Export `ModelRole`. `model_for` / `PROFILES` stay internal (`ws.model_id(role)` and `EngineConfig.models`).
5. **G18.** Export `record_usage` (meter a product's own model call into the active `measure_usage` scopes) and
   `traced_run` / `traced_step` (correlate a product's run and steps with the engine's traces). The generation-level
   tracing helpers stay internal.
6. **G19.** Export answer generation and relevance judgment as functions with their types:
   `agenerate_answer(query, evidence, *, ws)` (the model resolved from the workspace's roles, not a model object),
   `EvidenceItem`, `GeneratedAnswer`, `ajudge_spans(...)` (the batched span relevance judge, its model resolved
   from the workspace's roles the same way), `RelevanceVerdict`, `Relevance`, `Condition`. No query-embedder export
   (retrieval goes through the invokers; revisit only if a product shows a need).
7. **G20, weights.** One models root for every classifier: `RAG_MODELS_DIR` (and `EngineConfig.models_dir`), default
   the engine checkout's `data/models` as today; the property fleet and the clause classifier both resolve under it.
   A `scripts/fetch_reference_models.py` downloads the reference pack's two model sets (the clause-type SetFit
   ensemble, the 29-dimension fleet) as a checksummed archive from GCS. **Publishing the archive to GCS is an
   outward-facing step: its bucket, path and access are decided with you at that task.**
8. **G21, the pack SDK tier** (`rag_wright.pack_sdk`): a re-export module (the same objects, no fork) of the stable
   pack-author symbols; the reference pack is rewritten to import only `rag_wright.api` + `rag_wright.pack_sdk`, and
   a new import contract enforces it. Compatibility promise: the same as `rag_wright.api` (breaking changes only with
   a breaking release and a migration note).

## Tasks

Order: PS-1, PS-2, PS-3, PS-4, PS-5, PS-6 (query primitives) before PS-7 (store accessor) and PS-8 (pack SDK, which
needs both), then PS-9 (docs + release notes). One task, one approval, one commit.

| id | what | verify | status |
|---|---|---|---|
| PS-1 | G17: export `ModelRole` | export test; API docs regenerated | done: exported (same enum); API reference lists enum members; G17 closed |
| PS-2 | G16: `parse_document_bytes` / `aparse_document_bytes` exported; `IngestSource` bytes form; `build_ingestion` ingests bytes | hermetic tests; a live bytes ingest of a fixture | done: bytes entries exported; `IngestSource(data=, name=)`; builder + evaluate read bytes once; live PDF path vs bytes identical; G16 closed |
| PS-3 | G18: export `record_usage`, `traced_run`, `traced_step` | scope-attribution test (nested scopes, a product call recorded); tracing no-op without Langfuse | done: `record_usage`, `traced_run`, `traced_step` exported (same objects); live: engine + product calls metered together and both in the Langfuse session; G18 closed |
| PS-4 | G19: export `agenerate_answer(query, evidence, *, ws)`, `EvidenceItem`, `GeneratedAnswer`, `ajudge_spans(..., ws)`, `RelevanceVerdict` (+ `Relevance`, `Condition`) | hermetic tests with stubbed models; one live answer | done: exported with types (+`AnswerKind`), models from ws roles, final closed-vocab verdicts; judge moved to tag-parse (ADR-0045) after the live check found forced structured output failing on every call; G19 closed |
| PS-5 | G20: `RAG_MODELS_DIR` / `EngineConfig.models_dir` for every classifier; `scripts/fetch_reference_models.py` (checksummed archive); the GCS publish step agreed with you | loader tests (configured dir honoured by the fleet and the clause classifier); fetch script verified against the archive | done: `RAG_MODELS_DIR` + `models_dir()` for the fleet, clause ensemble and query LegalBERT (no `EngineConfig.models_dir`: fleets are process-wide); 23 archives (17.11 GB) + manifest at `gs://dreamai-pocs-ragwright-ingest/models/reference-pack/v1/` (private); 3 random archives fetched + prediction-identical; G20 closed |
| PS-6 | G21 query primitives: `kg_count`, `kg_delete`, `kg_update`, `key_range` on `kg_read` (Store protocol + ArcadeDB) | hermetic SQL tests; live store tests | todo |
| PS-7 | G15: `pack_store(ws, cls)`; the reference seam and the guides use it | seam tests (incl. empty catalog); no `ws._store` left in the reference pack, the guides or the skills | todo |
| PS-8 | G21: `rag_wright.pack_sdk`; the reference pack imports only `api` + `pack_sdk`; no raw `_query` / `_command` in the pack; import contract enforcing it | import contract kept; full suite; live Aimmune re-ingest record-identical | todo |
| PS-9 | Docs: API reference regenerated; the engine-usage + authoring skills, the domain-adaptation guide, the product-starter templates and the RuleWright guide updated (the interim rules in its Q&A replaced by the new surface); G15-G21 closed in `_engine-gaps.md`; ADR | doc-reference test; full suite | todo |
