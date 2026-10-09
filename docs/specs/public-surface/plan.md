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

Order: PS-1 ... PS-6 (query primitives), PS-7 (store accessor), PS-8a (the reference pack's store code onto the
primitives), PS-8b (pack SDK, which needs PS-7 and PS-8a), then PS-9 (docs + release notes). PS-8 was split
in two at the PS-6 gate so the store rewrite is its own visible task. One task, one approval, one commit.

| id | what | verify | status |
|---|---|---|---|
| PS-1 | G17: export `ModelRole` | export test; API docs regenerated | done: exported (same enum); API reference lists enum members; G17 closed |
| PS-2 | G16: `parse_document_bytes` / `aparse_document_bytes` exported; `IngestSource` bytes form; `build_ingestion` ingests bytes | hermetic tests; a live bytes ingest of a fixture | done: bytes entries exported; `IngestSource(data=, name=)`; builder + evaluate read bytes once; live PDF path vs bytes identical; G16 closed |
| PS-3 | G18: export `record_usage`, `traced_run`, `traced_step` | scope-attribution test (nested scopes, a product call recorded); tracing no-op without Langfuse | done: `record_usage`, `traced_run`, `traced_step` exported (same objects); live: engine + product calls metered together and both in the Langfuse session; G18 closed |
| PS-4 | G19: export `agenerate_answer(query, evidence, *, ws)`, `EvidenceItem`, `GeneratedAnswer`, `ajudge_spans(..., ws)`, `RelevanceVerdict` (+ `Relevance`, `Condition`) | hermetic tests with stubbed models; one live answer | done: exported with types (+`AnswerKind`), models from ws roles, final closed-vocab verdicts; judge moved to tag-parse (ADR-0045) after the live check found forced structured output failing on every call; G19 closed |
| PS-5 | G20: `RAG_MODELS_DIR` / `EngineConfig.models_dir` for every classifier; `scripts/fetch_reference_models.py` (checksummed archive); the GCS publish step agreed with you | loader tests (configured dir honoured by the fleet and the clause classifier); fetch script verified against the archive | done: `RAG_MODELS_DIR` + `models_dir()` for the fleet, clause ensemble and query LegalBERT (no `EngineConfig.models_dir`: fleets are process-wide); 23 archives (17.11 GB) + manifest at `gs://dreamai-pocs-ragwright-ingest/models/reference-pack/v1/` (private); 3 random archives fetched + prediction-identical; G20 closed |
| PS-6 | G21 query primitives: `kg_count`, `kg_delete`, `kg_update`, `key_range` on `kg_read` (Store protocol + ArcadeDB) | hermetic SQL tests; live store tests | done: on `Store` + `ArcadeDBStore` + `rag_wright.api`; shared filter grammar with `kg_edges`; `kg_update` changes only differing rows; live count/update/delete/key_range |
| PS-7 | G15: `pack_store(ws, cls)`; the reference seam and the guides use it | seam tests (incl. empty catalog); no `ws._store` left in the reference pack, the guides or the skills | done: `pack_store(ws, cls, *args, **kwargs)` exported; reference seam (7 uses) + entity-resolution and seam guides switched; guard test: no `ws._store` in packs/guides/templates/skills; G15 closed |
| PS-8a | G21: the reference pack's store code onto the primitives: every raw `_query` / `_command` / `_db` call in `ContractKGStore` (and `ComplianceStore`), incl. `write_clause_exception_links` (`kg_delete` + `kg_write`) and `clause_property_values` (`kg_edges`) | per-method parity tests; no `_query` / `_command` / `_db` left in `rag_wright.packs`; live re-ingest record-identical | done: all raw store calls in `ContractKGStore` on the primitives; `write_clause_exception_links` `_db` crash fixed; guard `test_packs_use_store_primitives`; seeded live parity tests; live pipeline checks pass except linking, which is bounded by the classifier (ADR-0126) |
| PS-R1 | Found in PS-8a's live check: every Qwen3.8-27b call hangs to its deadline while OpenRouter's `deepinfra/bf16` endpoint is down (0% uptime), because ADR-0111 hard-pins it with fallbacks off. Remove the provider routing entirely (your decision: unpinned; classifiers + Jev carry the critical decisions, FP8 validated on Modal); ADR-0125 supersedes ADR-0111 | profile tests; live seam + party extraction succeed during the outage | done: no provider routing on the three OpenRouter Qwen profiles; ADR-0125 supersedes 0111; live during the outage: seam 1.6 s, party extraction 8.0 s (pinned: hung to the deadline) |
| PS-R2 | A failed party (graph) extraction no longer dead-letters the document: the clause records are kept and the failure is reported | hermetic degrade test; live ingest keeps its clauses | done: `extract_graph` best-effort after its retries; `graph_failures` -> a `graph` kind in `build_partial_entry` (optional trailing arg) in both drivers; live: forced graph failure kept 6 clauses + 12 spans, no dead-letter |
| PS-R3 | Which member span represents a unit is a domain decision: engine hook `unit_representative` (a `UnitRepresentative`: the unit's member spans -> its anchor + leading tag) on `build_ingestion` / `IngestionStages`; the reference pack's rule chosen by measurement (heading-first vs operative span vs votes) on all 510 CUAD contracts against CUAD gold; docs tell products what the hook does and why | hook tests; CUAD rule comparison (local, CUAD outputs never committed); live ingest | done: `UnitRepresentative` hook; reference rule `provision_vote` (D) chosen on 510 CUAD contracts (56.6% vs 46.8% heading-first); probabilities via `with_probabilities` + `TaggedSpan.scores`; ADR-0126 |
| PS-R4 | A new domain can measure its unit labelling without a KG: `evaluate_ingestion` gains `span_tagger=`, `unit_representative=` and `unit_labels=` (gold: per document, each unit by a text snippet with its expected label) and reports label accuracy, per-label results and confusion pairs; the creating-evals skill gains a unit-labelling entry; building-an-ingestion-capability says to build this gold before choosing a `unit_representative`; the CUAD run is the documented reference example (numbers only) | hermetic tests; a worked example on sample documents | todo |
| PS-R5a | Domain vocabulary out of the generic engine, public surface first (blocks 0.3.0): the relevance judge (`Condition.clause_type` -> `category`, its SKILL.md + prompt) and the generation method become domain-neutral, with the contract wording in a reference-pack guidance overlay; neutral discovery text in the engine capability manifests; neutral docstring examples; a vocabulary guard test (zero tolerance in engine skills, manifests and public API docstrings; a per-file ratchet elsewhere) complementing the import-linter | guard test; relevance + generation tests; live judge + answer | done: neutral relevance judge (`Condition.category`) + generation method with pack `guidance` overlays; neutral manifests + docstrings; vocabulary guard (zero tolerance + ratchet at 156 lines / 24 files); live judge + answer |
| PS-R5b | RLM chunking prompts domain-neutral (domain hints supplied by the pack) and a `build_ingestion(chunk_discoverer=)` hook + `default_chunk_discoverer(guidance=)` so a product can set them; the entity-canonicalisation word lists (party words, party-definition fragments) moved into the pack `.ttl` (ADR-0066) | guard ratchet lowered; chunking + canonicalisation tests; live ingest | done: neutral chunking prompts with `guidance=` (pack passes contract guidance); `build_ingestion(chunk_discoverer=)` + `default_chunk_discoverer(guidance=)` + `BoundaryDiscoverer` exported; `EntityRules` with the contract roles on `cbr:entityRules` in the `.ttl`; ratchet 156 -> 133 lines; live: same 2 entities + 1 edge, over-cap chunking split per guidance |
| PS-R5c | Retire OKF (user decision; was 'move into the pack'): `rag_wright/okf/`, the `okf_navigate` capability + skill, the `okf_compile`/`okf_navigate` pack slugs, `ModelRole.OKF_ENRICHMENT`, the OKF tests and the experiment's eval scripts; ADR-0127 | full suite; catalog + discover live; ratchet lowered; no dangling imports | done: OKF code, capability, skill, slugs, `OKF_ENRICHMENT` role, tests and experiment evals removed; ADR-0127; ratchet 133 -> 66 lines; catalog 35, no OKF |
| PS-8b | G21: the `rag_wright.pack_sdk` tier (D1: re-exports of the pack-author building blocks not on `api`, same compatibility promise); D2: the generic private helpers the pack used promoted under public names; D3: `StoreConfig.from_env()` so a pack's standalone entrypoints open a workspace instead of the backend; the SDK's generated reference | export-identity tests; generated reference current; full suite | done: `rag_wright.pack_sdk` (83 re-exports, disjoint from `api`, 4 clash renames); 9 private helpers promoted; `StoreConfig.from_env()`; `docs/api/pack_sdk.md` generated (deterministic signatures); guard covers both tiers; live from_env + pack_store |
| PS-8c | G21: the reference pack imports only `rag_wright.api` + `rag_wright.pack_sdk` (+ itself); its MCP servers open workspaces + `pack_store`; an import contract enforces it (D4) | import contract; full suite; live re-ingest record-identical | done: 172 imports rewritten by object identity (52 files), zero engine-internal imports left in packs; MCP servers open workspaces via `StoreConfig.from_env()` + `pack_store`; import contract (direct imports, proof test); live re-ingest identical (7 clauses, 2 entities, 1 edge, 14 spans) |
| PS-9 | Docs: API reference regenerated; the engine-usage + authoring skills, the domain-adaptation guide, the product-starter templates and the RuleWright guide updated (the interim rules in its Q&A replaced by the new surface); G15-G21 closed in `_engine-gaps.md`; ADR; product-starter templates (CLAUDE.md + playbook + README) and the `using-the-rag-wright-engine` skill: define an `engine-docs` Graphify lane (`{{ENGINE_DOCS_LANE_PATH}}`: the engine repo's `docs/` + skills at the tag matching the installed engine) next to the `engine` lane, and a standing rule to rebuild both engine lanes on every engine version change (Dependabot merge, `uv sync`, editable-mode edits) before grounding the next call; skill lessons from PS-R3 (ADR-0126) in `setfit` and `creating-evals`: evaluate a classifier the way its consumers use it (top-1 accuracy when anything acts on the primary label, alongside top-k for soft tags; on pipeline-produced inputs incl. headings and no-label spans, not only curated snippets) and list the consumers of the primary label (and check them end to end) before declaring a classifier swap has no downstream impact; fix the stale `clause_function_classification` manifest description (says LegalBERT; production is the SetFit ensemble) | doc-reference test; full suite | done: RuleWright 0.3.0 guide; G21 closed, G1/G13 mostly closed (SDK +GraphWriter/build_graph_extraction, 85); skills (two tiers, engine-docs lane + rebuild rule, classifier-eval lessons, bytes sources); templates; ER/authoring/config guides; manifest text; ADR-0128; tasks.md + CLAUDE.md banner |
