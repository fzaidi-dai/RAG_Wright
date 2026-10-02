# Handoff: migrating the product onto the new engine API (RuleWright)

Status: **LIVING FIRST DRAFT** (2026-10-02). Updated as the engine-platform work lands; the **final** version is the
cross-repo handoff the product (RuleWright) executes. **No product-side changes are made from the engine repo** — this
doc captures *what will need doing* on the product side, so the migration is planned, not discovered.

## Why this exists

The engine (RAG_Wright) is being turned into a clean, domain-agnostic **platform with a stable API**, so a product
builds on it without reaching into its internals. Two things changed:

1. **The engine core was de-domained** (R1): `store/arcadedb.py` imports no contract/compliance contract; all domain
   writes + jurisdiction canonicalization moved to capability-layer store extensions.
2. **An engine API layer is being published** (R2, `rag_wright/api/`): an opaque workspace + per-kind capability
   invokers, so the product talks to the engine through a stable surface — never `ArcadeDBStore`, `query_embedder`,
   model ids, or engine id formats.

**Consequence for the product:** RuleWright's `engine/seam.py` today mixes three concerns — generic engine
orchestration (G), engine infrastructure (I), and genuine product/domain logic (D). The engine now provides G + I
through its API. So at handoff the product seam is **rewritten**: delete the G/I reimplementations (call the API
instead), and **re-implement the D functions on the new API**. This holds for any new functions the product adds —
there is **no product seam or adapter inside the engine**; each product owns its seam + its API-on-top, built on the
engine API (ADR-0052: Product → Engine, one-way, never the reverse).

## Basis documents (read these for the full rationale)

- **ADR-0117** `docs/adr/0117-engine-api-layer-and-capability-runtime.md` — the engine API layer + capability runtime
  (the governing decision): capability kinds, the opaque `WorkspaceHandle`, per-kind invokers, the invoker as an ARD
  *client* (ARD stays metadata; no second registry), progressive loading, de-domaining as prerequisite.
- **ADR-0067** `docs/adr/0067-...md` — de-domaining the KG/entity layer (R1).
- **ADR-0066** — knowledge in the ontology `.ttl`, code is mechanism.
- **ADR-0052** — the engine/product split (open-core engine; product is a separate repo; one-way dependency).
- **ADR-0003** — ARD registration is metadata (no callables); the basis for "the invoker is a client, ARD stays
  metadata."
- **ADR-0115 / 0116** — the classifier-first Step-3a + soft function-scoping (the 29-dim fleet → the lane-level
  `clause_property_classification` capability).
- **Proposals** (the design narrative): `docs/proposals/de-domaining-and-capability-runtime.md` (engineering plan) and
  `docs/proposals/new-domain-developer-journey.md` (the target-state developer journey — the acceptance test).
- **Child spec + task ledger**: `docs/specs/engine-platform/SPEC.md` + `TASKS.md` — the active workstream, its
  requirements (R1–R5), acceptance criteria, and per-task status.

## The seam split

| Bucket | What | Where it goes |
|---|---|---|
| **G — generic engine orchestration** | parse, build source document, ingest + QA + corpus-retrieval orchestration, `generate_answer`, shape adapters, bulk corpus ingest, `_ByteCorpus` | **DELETE from the product** → call the engine API |
| **I — engine infrastructure** | `ArcadeDBStore` construction, `query_embedder` construction, model-profile/model-id knowledge, `export_engine_env` (var-name bridge), engine id/format parsers (`decode_bbox`/`document_of`/`policy_of_requirement`), per-customer store/graph caches | **DELETE from the product** → hidden behind the engine API |
| **D — product/domain logic** | the compliance leg, clause-taxonomy surface, party-exposure vocabulary, citation-preview types, tenancy/scoping, observability wiring, caching policy | **KEEP in the product**, re-implemented on the new API |

### D — product-owned functions to re-implement on the new API

- **Compliance leg (orchestration + vocabulary):** `invoke_compliance_check`, `invoke_compliance_document_check`,
  `invoke_ad_compliance_check`, `invoke_ad_compliance_document_check`, `invoke_policy_ingest`, `requirements_for`,
  `requirement_locations`, `curated_requirement_count`, `gated_pairs`, `policy_of_requirement`, `UnknownPolicy`,
  `RequirementLocation` — re-implement over `ainvoke_subgraph("compliance_*")` + `kg_read` (EP-API-3), not a raw store.
- **Clause-taxonomy surface:** `canonical_clause_type`, `clause_type_vocabulary`, `understand_question` output
  semantics.
- **Party-exposure vocabulary:** `find_party`, `party_counterparties`, `party_affiliates`, `contract_terms`,
  `party_traversal_for` — the "counterparty/affiliate" naming over the engine's generic graph traversal.
- **Citation-preview product types:** `SpanLocation`, `RequirementLocation`, `span_locations` (position assembly for
  the UI).
- **Tenancy + scoping policy:** per-customer DB routing (`databases_for`), the workspace `documents` selection,
  `ScopeViolation` (confidentiality guard).
- **Observability wiring:** `record_session`/`record_run`, `UsageReport`, `measure_usage`/`measured`/
  `report_from_usage` — mapping the engine's in-band usage into product telemetry.

### MIXED functions split

The generic *mechanism* goes to the engine; only the domain *vocabulary/wrapper* stays in the product. E.g.
`party_counterparties` = engine graph traversal (via an invoker / `kg_edges`, EP-API-3) **+** the product's
`CONTRACTS_WITH`/"counterparty" naming.

### Deleted from the product seam (the engine API now provides these)

| Old product-seam construct | New engine API |
|---|---|
| `build_compliance_store` / `ArcadeDBStore.from_env` | `engine.open_workspace(config, corpus=...)` → opaque `WorkspaceHandle` |
| `export_engine_env` (var-name bridge) | `engine.EngineConfig` / `StoreConfig` (typed config) |
| import `production_relational_qa` / `_intra_document_qa` / `_typed_property_retrieval` + build + `.ainvoke` | `await engine.ainvoke_subgraph(name, inputs, resources=ws)` |
| the LegalBERT classifier wiring | `engine.invoke_model("clause_function_classification", {...}, resources=ws)` |
| `query_embedder()` construction | hidden behind the handle (embedding = a config profile) |
| `answer_model_for` / `default_extraction_model` / `judge_model_id` / pinned model id | model **aliases** in `EngineConfig.models`; the handle's `model_id(role)` |
| `decode_bbox` / `document_of` / `policy_of_requirement` (id parsers) | engine id/format accessors (`document_of`/`id_source`/`decode_bbox`) |
| per-customer store/graph caches (`resources.py`) | absorbed by `open_workspace` caching |
| `requirements_for` / `span_properties` / scoped KG reads | `engine.kg_read(ws, ...)` |

## The engine API surface (done vs pending)

- **Done (EP-API-1):** `EngineConfig`/`StoreConfig`, `open_workspace(config, *, corpus) -> WorkspaceHandle` (opaque;
  `corpus` = the backend database name — the product maps its tenant → db-name; `model_id(role)` resolver).
- **Done (EP-API-2):** `ainvoke_subgraph(name, inputs, *, resources)` + `invoke_model(name, inputs, *, resources)` +
  `capability_index()` (discovery). The invoker is a progressive-loading **ARD client** (light index from the ARD
  manifest specs; lazy per-capability adapter). First adapters wired: `typed_property_retrieval`,
  `clause_function_classification`.
- **Done (EP-API-3):** `kg_read(ws, …)` / `kg_write(ws, …)` + `span_positions(ws, document)` (scoped KG access over
  the handle), and id/format accessors `document_of` / `id_source` / `decode_bbox` — so the product does KG reads +
  citations without `ws._store` or id-string parsing. (`import rag_wright.api` stays light — lazy internals.)
- **Done (EP-API-2b):** subgraph adapters for `contract_ingestion_pipeline` (ingestion), `relational_qa`,
  `intra_document_qa` + `api.source_document(document_id, text=...)` to feed the ingestion capability. So the QA,
  relational, and ingestion D-functions are now migratable.
- **Pending (EP-API-2c):** `invoke_function`/`ainvoke_function` + agent-skill invokers (function/skill capabilities
  are internal building blocks, rarely invoked standalone by a product).
- **Done (EP-API-4a):** the **ingest options catalog** — `EngineConfig(store=…, options=EngineOptions(ingest=IngestOptions(…)))`
  carries the ingest knobs (`classify_concurrency`, `clause_concurrency`, `affiliations`, `function_classifier`,
  `list_model`, `clause_samples`), each defaulting to `None` = the engine default. So the product sets ingest behavior
  through config instead of environment variables (`CLASSIFY_CONCURRENCY`, `RAG_INGEST_AFFILIATIONS`,
  `RAG_FUNCTION_CLASSIFIER`, …). Env stays the fallback, and leaving options unset reproduces today's behavior exactly.
- **Done (EP-API-4b):** **pluggable embedder by profile** — `EngineConfig(embeddings={"text": "<profile>"})` selects
  both the query-side and ingest-side embedders via the engine's profile seam (`capabilities/embedding_profiles.py`);
  default `bge-m3` unchanged, BGE-M3 no longer hardcoded. A new embedder family is one engine-side registry entry,
  invisible to the product.
- **Pending (EP-API-4c):** pluggable parser (docling choice via config).
- **Done (EP-E2E):** full-stack live proof — a real doc INGESTED + QUERIED entirely through `rag_wright.api`
  (open_workspace → source_document → ainvoke_subgraph ingest → query); the developer-journey path works end-to-end.
  (Pending: a non-contract smoke domain, AC-journey.)
- **Done (EP-RT-1):** the 29-dim property-classifier fleet is a lane-level **`model`** capability —
  `engine.invoke_model("clause_property_classification", {"text": <provision>, "functions": (<clause functions>,)}, resources=ws)`
  returns `[{dimension, value, confidence}]` soft tags (the classifier lane of Step-3a, no LLM). The fleet loads once
  per process (cached). Invokable from a graph by direct import too.
- **Done (EP-RT-2):** the **generic capability→MCP adapter** — `from rag_wright.api.mcp import build_capability_mcp`
  (a submodule import: `api.mcp` pulls `fastmcp`, kept out of `rag_wright.api`'s light surface). `build_capability_mcp(slug, *, resources=ws)`
  returns a `FastMCP` server exposing ANY catalogued invokable capability (subgraph/model) as one tool — name/title/
  description from the ARD manifest, handler dispatching through the engine invoker over the bound workspace, output
  in a uniform `{"result": <json>}` envelope. `serve_capability_mcp(slug, *, resources, transport="stdio")` serves it.
  The tool takes an opaque `inputs` dict (the capability's own input contract). Use this for zero-boilerplate MCP
  exposure of a capability or a new-domain graph; the 4 bespoke Tier-1 servers in `rag_wright/mcp/` remain for
  curated, typed tool signatures. The product owns tenant→corpus routing (build one server per workspace).
- **Done (EP-API-6):** **PDF/docling ingest entry point** — `engine.parse_document(id, path, *, cache_dir, metadata=None)`
  (sync) / `engine.aparse_document(...)` (async) run the engine's real docling parse and return a structure-bearing
  `SourceDocument` (`.parsed` set), which you then pass as the `document` input of `ainvoke_subgraph("contract_ingestion_pipeline", …)`.
  So the product's byte-source ingest (PDF/DOCX/HTML/MD) now runs through the API; `source_document(id, text=…)` stays
  for already-text input. (Docling parse was the last piece of G-bucket "parse" still product-side.)
- **Done (EP-API-5):** **usage/cost on the API surface** — `with engine.measure_usage() as u: await engine.ainvoke_subgraph(...)`
  then read `u.calls / u.input_tokens / u.output_tokens / u.cost_usd / u.calls_without_cost / u.latency_ms_total /
  u.by_model` (`{model_id: ModelUsage}`). This is the public face of the engine's in-band usage accounting
  (ADR-0105), so the product's telemetry layer (`record_session`/`UsageReport`/`report_from_usage`, D-bucket) wraps
  `measure_usage()` instead of importing `rag_wright.models.usage`. Capturing is opt-in and additive across nesting
  (a task-level scope totals everything; inner scopes attribute their slice); with no scope, zero overhead. The
  invokers no longer open their own scope — usage is the caller's concern.

**Migration note:** with EP-API-3 landed, the compliance leg + citation-preview + party-exposure reads can now be
re-implemented on `kg_read` + the id/format accessors; the QA/retrieval D functions migrate onto EP-API-1/2. The
ingestion D functions can now migrate onto `ainvoke_subgraph("contract_ingestion_pipeline", {document, cache_dir})`
+ `api.source_document`.

## Before / after (illustrative)

```python
# BEFORE (product seam, today): reaches into engine internals
store = build_compliance_store(settings, databases=dbs)          # raw ArcadeDBStore
report = await invoke_compliance_check(store, subject_text=t, sources=[...])

# AFTER (on the engine API): opaque handle + invoker, no store/embedder/model in the product
ws = engine.open_workspace(cfg, corpus=dbs.compliance)
report = await engine.ainvoke_subgraph("compliance_check", {"subject_text": t, "sources": [...]}, resources=ws)
```

```python
# BEFORE: import the leg builder, wire store + model, .ainvoke
leg = production_typed_property_retrieval(store=store, embedder=query_embedder(), extract_model=...)
out = await leg.ainvoke({"query": q})

# AFTER
out = await engine.ainvoke_subgraph("typed_property_retrieval", {"query": q}, resources=ws)
```

## The principle (confirmed)

- **No product-specific seam or adapter lives in the engine, now or later.** The engine ships (a) the domain-agnostic
  API (`open_workspace`, the invokers, `kg_read`/`kg_write`, id/format accessors, the options catalog) and (b) the
  reference-domain-pack capabilities. Nothing else that is product glue.
- **Each product implements its own seam** (the D functions, re-implemented on the API) **and its own API-on-top**
  (web API / agent tools / auth / tenancy). Any new product function lives in that product's seam, on the engine API.
- **Product → Engine only** (ADR-0052), enforced by an import-linter on the product side at handoff.

## Product-side acceptance (at handoff)

- The product imports **nothing** from `rag_wright.store.*`, builds no embedder, holds no model id — only the engine
  API (`rag_wright.api`) + the capability names it invokes.
- Every migrated D function has a **live** test on the new API (the engine's live-testing discipline carries over).
- The import-linter rule (Product → Engine; product imports only `rag_wright.api` + contracts, not internals) is in
  the product CI.

## Changelog
- 2026-10-02 — first draft (after EP-API-1 + EP-API-2).
- 2026-10-02 — EP-API-3 landed: `kg_read`/`kg_write`/`span_positions` + `document_of`/`id_source`/`decode_bbox`;
  compliance/citation/party-read D-functions migratable.
- 2026-10-02 — EP-API-2b landed: ingestion + relational + intra-doc-QA subgraph adapters + `source_document`;
  ingestion/QA/relational D-functions migratable.
- 2026-10-02 — **EP-E2E PASSED**: a real doc ingested + queried entirely through `rag_wright.api` (the migration
  target path is proven). (Update as EP-API-2c/4 + R3 land, and when the compliance opaque-handle migration — EP-SEAM
  — is specced.)
