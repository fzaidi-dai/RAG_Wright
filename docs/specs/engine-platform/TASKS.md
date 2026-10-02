# Tasks: The Engine Platform Boundary

Child task ledger for `./SPEC.md` (child of the root `SPEC.md`/`tasks.md`). Governs ADR-0117 + the ADR-0067
continuation. Runs under the main working loop (CLAUDE.md): one task, contract-first TDD + a live A/B or smoke test,
approval gate, atomic commit. Statuses: `todo | in-progress | awaiting-approval | done`.

> **RESUME / NEXT UP (2026-10-02):** R1 AC-no-leak COMPLETE (DD-1a/b/c). **EP-API-1 + EP-API-2 DONE.** API-1:
> `EngineConfig`/`open_workspace` -> opaque `WorkspaceHandle`. API-2: the invoker as a **progressive-loading ARD
> client** (`api/invoke.py`) — light index from manifest specs (no impl imports) + `ainvoke_subgraph`/`invoke_model`
> over the handle (lazy adapter resolution, name/kind validation vs the ARD catalog, usage+trace hardening, drift
> guard). 2 adapters wired (typed_property_retrieval, clause_function_classification); live: real LegalBERT classify +
> **real subgraph leg end-to-end over a workspace via the API** (EP-E2E starting). Full suite 1653 pass.
> **EP-API-3 + EP-API-2b DONE.** API-3: `api/kg.py` + `api/ids.py` (scoped KG access + id/format accessors over the
> handle; lazy so the API import stays light). API-2b: 3 subgraph adapters wired (`contract_ingestion_pipeline`,
> `relational_qa`, `intra_document_qa`) + `api.source_document`; live query-leg adapters end-to-end over a workspace.
> **EP-E2E DONE (acceptance milestone)** — full-stack live: a real doc INGESTED + QUERIED entirely through
> `rag_wright.api` (open_workspace → source_document → ainvoke_subgraph ingest → query), nothing touching
> ArcadeDBStore/query_embedder/model ids; PASSED in 147s. Also fixed the ingest adapter to use the pipeline's BGE-M3
> SPAN embedder (not the query embedder). The developer-journey path is proven end-to-end.
> **EP-RT-1 + EP-RT-2 DONE (R3).** RT-1: the 29-dim classifier fleet is a lane-level `clause_property_classification`
> **model** capability — `HybridPropertyExtractor.classify_properties(...)` + ARD slug/manifest + an `invoke_model`
> adapter (process-cached fleet). RT-2: `api/mcp.py::build_capability_mcp(slug, *, resources)` — the GENERIC
> capability→MCP adapter (any invokable subgraph/model → a FastMCP tool, ARD-driven, no bespoke code; uniform
> `{"result": …}` envelope). Live for both: the real fleet fires `dispute_method` via `invoke_model` AND via the
> generic MCP tool end-to-end. Full suite 1668 pass.
> **Next up (your pick):** **EP-RT-3** (per-kind authoring skills), **EP-RT-1b** (sweep: every slug gets an adapter or
> an internal-only marker), **EP-API-4** (options catalog + pluggable embedders), the **non-contract smoke domain**
> (AC-journey), or **EP-API-2c** (`invoke_function`/agent-skill).
> **DD-2 + DD-1d DEFERRED**. Handoff: `docs/product/engine-api-migration-handoff.md`.
>
> **LIVE-TESTING POLICY (standing):** every task ends with a LIVE test on real infra where applicable (AC-parity),
> not just hermetic — done each gate so far (DD-1b 15 live store tests; DD-1c live smoke; EP-API-1 live workspace).
> **Heavier end-to-end gate (EP-E2E, after EP-API-2/3):** stand up a workspace via the engine API and run a real
> document through ingest + a query leg entirely through the invokers (+ eventually a minimal non-contract smoke
> domain per AC-journey) — the full-stack live proof, scheduled once the invokers exist.

## R1 — De-domain the core (extends ADR-0067; prerequisite). Each: TDD + no-behavior-change live A/B.

| id | task | implements | status | files | verify |
|---|---|---|---|---|---|
| DD-1a | Add the generic, backend-agnostic typed-node read **`Store.kg_read`** (equality/`IN` filters, `fields`/`distinct`/`order_by`/`limit`, empty-list→`[]`) + impl; re-express `all_requirements`/`contract_by_id`/`spans_by_contract` onto it with SQL-parity. Proves `kg_read` adequate before DD-1b's migration. | R1, ADR-0117 | **done (2026-10-02)** | `store/seam.py`, `store/arcadedb.py`, `tests/store/test_kg_read.py`, `tests/store/test_arcadedb_schema.py` (stub) | 7 contract + 3 parity tests green; full suite 1632 pass, 0 fail |
| DD-1b | Add generic **`kg_write`** (typed node + edge upsert, schema-driven encoding via `_property_types`); re-express the WRITES (`write_clause_kg`/`write_property_graph`/`upsert_contract`/`write_requirements` + `_clause_kg_statements`) into capability-layer **`ContractKGStore`** + **`ComplianceStore`** extensions; re-key `_TYPED_DIMENSION_EDGE` off the str map (drops `PropertyDimension`); **remove `contracts.{compliance,contract_meta,property}` imports from `store/arcadedb.py`**; switch callers (pipeline + `compliance_ingestion` write-seam + 5 scripts); extend the domain-neutral guard. | R1, ADR-0067 P5b, AC-no-leak | **done (2026-10-02)** | `store/{arcadedb,seam}.py`, `subgraphs/{contract_ingestion_pipeline,compliance_ingestion}.py`, new `capabilities/{contract_kg_store,compliance_store}.py`, 5 scripts, `tests/*` | `kg_write` 8 tests + statement parity + guard green; **15 live store tests** (real ArcadeDB) pass; full suite 1643 pass |
| DD-1c | Move `patch_canonical_jurisdictions` → `ContractKGStore` (re-expressed on `kg_read` + `kg_write` partial-upsert; `canonical_value` is pack-declared), dropping the last domain import (`contracts.jurisdiction`); tighten the domain-neutral guard to an ALLOWLIST (store imports only chunk/provenance/span). **Completes R1 AC-no-leak.** | R1, AC-no-leak | **done (2026-10-02)** | `store/arcadedb.py`, `capabilities/contract_kg_store.py`, `tests/capabilities/test_contract_kg_store.py`, `tests/store/test_engine_domain_neutral.py` | allowlist guard green; hermetic + live smoke pass; full suite 1644 pass |
| DD-1d | **(store-purity, DEFERRED — not a leak)** Relocate the edge-TRAVERSAL reads (`clause_typed_edges`/`contract_clause_kg`/`exceptions_of_clause`/`span_properties`/`clauses_with_property`) via a generic **`kg_edges`** traversal primitive (3 ArcadeDB idioms + contract-scope range) + the remaining dict-returning node reads → the extensions, so the store holds no domain METHODS. These import nothing, so they don't block AC-no-leak. | R1 | deferred | `store/arcadedb.py`, `store/seam.py`, `capabilities/contract_kg_store.py`, callers, `tests/store/*` | traversal reads identical live; store has no domain method |
| DD-2 | De-contract the generic infra columns: `Span.contract_id` → `source_doc_id`; `Span.function` → `span_label`. | R1 | **deferred** (naming purity, ~76-site churn, NOT a leak; `function` rename debatable + would split Span/Clause naming). AC-no-leak already met. Revisit only if a new domain finds the names confusing. | `store/arcadedb.py` DDL + readers/writers | identical live schema + span round-trip |
| DD-3 | Extract the `EntityResolver` seam (injected `(mention clusters) → canonical ids`); EDGAR-CIK = one impl (SEC pack default); generic default = the exact-normalized surface-form registry. | R1, ADR-0067 P5c | todo | `capabilities/entity_resolution.py`, `store/seam.py`, pipeline resolve/write wiring | SEC corpus resolves identically with CIK resolver injected; non-SEC doc resolves via surface-form default |
| DD-4 | Genericize the `EntityId` contract: drop the 10-digit-CIK validator; `EntityId` = canonical-id string (FR-S.3 scheme unchanged); CIK format → the SEC resolver. **Ask-first identifier — flagged.** | R1, ADR-0067 P5c, FR-S.3 | todo | `contracts/identifiers.py` + consumers | identifiers round-trip; no fabricated ids |
| DD-5 | Entity/edge taxonomy → pack `.ttl`: `EntityType`/`RelationshipType` enums + `graph_query`'s `CONTRACTS_WITH` default become pack-declared (enables the MIXED-seam split). | R1, ADR-0066 | todo | `contracts/ontology.py`, `graph_query.py`, `graph_extraction.py`, `entity_resolution.py` | contract taxonomy identical when the contract pack is loaded |
| DD-6 | Enforcement: add `import-linter` — (i) engine must not reference `cik`/`edgar`/the contract pack; (ii) Product→Engine. Decide where CI runs (no `.github/workflows/` yet). | R1, AC-no-leak, ADR-0067 #5 | todo | `pyproject.toml`/`.importlinter`, CI config | a deliberate violating import fails the linter |

## R2 — Engine API layer (ADR-0117). Each: contract-first TDD + a live smoke test.

| id | task | implements | status | files (proposed) | verify |
|---|---|---|---|---|---|
| EP-API-1 | `open_workspace(config, *, corpus) -> WorkspaceHandle` (opaque; resolves+caches store, lazy embedder; ensures schema) + `EngineConfig`/`StoreConfig` (backend connection, model aliases, embedding profile; `options` catalog -> EP-API-4) + `model_id(role)` resolver + `ArcadeDBStore.from_config` (de-env'd). `corpus` = db-name (tenancy product-side). | R2 | **done (2026-10-02)** | `rag_wright/api/{__init__,config,workspace}.py`, `store/arcadedb.py` (from_config), `tests/api/test_workspace.py` | 3 hermetic + 1 live (real ArcadeDB round-trip + caching + opaque); full suite 1647 pass |
| EP-API-2 | The invoker as a **progressive-loading ARD client**: light index from the ARD manifest specs (`capability_index`, no impl imports); `ainvoke_subgraph` + `invoke_model` over the opaque handle — name/kind validation vs the ARD catalog, LAZY adapter resolution (lazy impl import), `usage_scope` + trace span; drift guard (every wired adapter ∈ catalog, kind matches). ARD stays metadata; the binding is the client's. First-slice adapters: typed_property_retrieval, clause_function_classification. | R2, AC-runtime | **done (2026-10-02)** | `rag_wright/api/{invoke,__init__}.py`, `tests/api/test_invoke.py` | 5 hermetic + 2 live (real LegalBERT via invoke_model; real subgraph leg end-to-end over a workspace); full suite 1653 pass |
| EP-API-2b | Subgraph adapters: `contract_ingestion_pipeline` (ingestion; unblocks EP-E2E), `relational_qa`, `intra_document_qa` + `api.source_document` (build a doc for ingest). | R2, AC-runtime | **done (2026-10-02)** | `rag_wright/api/{invoke,documents,__init__}.py`, `tests/api/test_invoke.py` | drift guard over all 5 adapters + source_document; 2 live query-leg adapters end-to-end; full suite 1660 pass |
| EP-API-2c | Remaining invoker kinds (`invoke_function`/`ainvoke_function`, agent-skill) + uniform light retry/timeout for function/model + co-register adapters per capability module (vs the central binding). | R2, R3, AC-runtime | deferred | `rag_wright/api/invoke.py`, capability modules | each kind invocable by name; adapters co-located; drift guard green |
| EP-E2E | **Acceptance:** full-stack live proof — a real doc INGESTED + QUERIED entirely through `rag_wright.api` (open_workspace → source_document → ainvoke_subgraph ingest → query), nothing reaching ArcadeDBStore/query_embedder/model ids. (Also fixed the ingest adapter to use the pipeline's BGE-M3 SPAN embedder.) Non-contract smoke domain (AC-journey) = a follow-on. | R2/R3, AC-journey | **done (2026-10-02)** | `tests/api/test_e2e.py`, `rag_wright/api/invoke.py` | PASSED (147s): ingest wrote the clause KG (scoped to the doc) + the query leg ran over it, all via the API |
| EP-API-3 | Generic `kg_read(ws, …)` / `kg_write(ws, …)` + `span_positions(ws, doc)` over the opaque handle, and id/format accessors (`document_of`, `id_source`, `decode_bbox`) — lazy-imported so `import rag_wright.api` stays light. The product never parses engine id strings or touches `ws._store`. | R2 | **done (2026-10-02)** | `rag_wright/api/{kg,ids,__init__}.py`, `tests/api/test_kg.py` | 6 hermetic + 1 live (round-trip over a real workspace); API import stays store-free; full suite 1659 pass |
| EP-API-4 | Embedding + model as **options** (by alias/profile) + pluggable embedder/parser capabilities; formalize the `options` catalog (reranker/retrieval/chunking/ingest knobs) with defaults, replacing the six leaked ingest env-vars. | R2 | todo | `rag_wright/api/config.py`, model/embedding profile seams | a non-default embedder/model/knob is selected via config; defaults unchanged behavior |

## R3 — Capability runtime (ADR-0117). 

| id | task | implements | status | files (proposed) | verify |
|---|---|---|---|---|---|
| EP-RT-1 | Register the **lane-level `clause_property_classification` `model` capability** (the 29-dim fleet): `HybridPropertyExtractor.classify_properties(text, *, functions=())` (the classifier lane as a standalone entry) + the ARD slug/manifest + an `invoke_model` adapter with a process-cached registry. Invokable from graphs (direct import, as the ingest pipeline already does) and products (`invoke_model(...)`). (A full "every slug has an adapter or an internal-only marker" sweep = a follow-on EP-RT-1b.) | R3, AC-runtime, ADR-0115/0116 | **done (2026-10-02)** | `spans/property_extractor.py`, `capabilities/{registry,manifests}.py`, `rag_wright/api/invoke.py`, `tests/api/test_invoke.py` | index carries the slug (kind=model) + drift guard green; hermetic dispatch (usage scope opens); **live: the real 29-dim fleet fires `dispute_method` via `invoke_model` through the API**; full suite 1663 pass |
| EP-RT-2 | Generic capability→MCP adapter: `api/mcp.py::build_capability_mcp(slug, *, resources)` exposes any catalogued **invokable** capability (subgraph/model) as a FastMCP server with NO bespoke code — tool name/title/description from the ARD manifest, handler dispatches through the engine invoker over the bound workspace, uniform `{"result": …}` JSON envelope; `serve_capability_mcp` convenience. Opaque `inputs` dict (no JSON input schema in ARD to type it); store binds server-side (issue 0035). The 4 bespoke Tier-1 servers STAY (curated typed tools) — not retired. Submodule import (keeps `import rag_wright.api` light). | R3, AC-runtime | **done (2026-10-02)** | `rag_wright/api/mcp.py`, `tests/api/test_mcp.py` | 4 hermetic (subgraph + model exposed; reject non-invokable kind + unknown slug) + **1 live: the real 29-dim fleet fires `dispute_method` through the generic MCP tool end-to-end**; full suite 1668 pass |
| EP-RT-3 | Per-kind capability-authoring skills (subgraph/function/model/agent_skill) + the registration+ARD+invocation contract; wire into CLAUDE.md/playbook. | R3 | todo | `.claude/skills/`, `docs/playbook.md` | a new capability of each kind can be authored by following the skill |
| EP-RT-4 | **(later)** Resumability/interruptibility via the LangGraph checkpointer (store-backed) + `interrupt`/resume + a progress contract. | R5 | todo | `subgraphs/scaffold.py`, `rag_wright/api/` | a long run resumes after interruption with no recompute of completed nodes |

## R3b — Reference domain pack on the new API (worked examples that de-risk the product migration).

> **Why (2026-10-02, user):** implement the D-bucket domain functions ourselves, in the engine, as TESTED reference
> implementations over the new API — so the product gets correct, properly-working examples + key domain-seam logic
> already written in the new paradigm, minimizing product-side work. **ADR-0052 boundary (load-bearing):** these are
> the **reference domain pack** (the contract/compliance worked example that keeps the open-core runnable + demoable),
> NOT the product's seam verbatim. So the line is: domain data/vocabulary logic that is a pure composition over the
> engine API lives here as reference; genuinely product-only concerns (tenancy/DB routing `databases_for`,
> `ScopeViolation` confidentiality guard, observability→product telemetry, UI citation-preview types, the orchestration
> harness + product-named tools) STAY product-only (handoff D / EP-SEAM-3). EP-REF-1 is the source EP-SEAM-3 lifts.

| id | task | implements | status | files (proposed) | verify |
|---|---|---|---|---|---|
| EP-REF-1a | **Prereq (= DD-1d, un-deferred):** add a generic `kg_edges` traversal primitive to the store (3 ArcadeDB idioms + contract-scope range) + relocate the edge-traversal reads (`clause_typed_edges`/`contract_clause_kg`/`exceptions_of_clause`/`span_properties`/`clauses_with_property`) into `ContractKGStore` over it. Needed because the D reads (party traversal, requirement/clause locations) are edge traversals. | R1, R3b | todo | `store/{arcadedb,seam}.py`, `capabilities/contract_kg_store.py`, `tests/store/*` | traversal reads identical live vs today; store holds no domain traversal method |
| EP-REF-1b | The domain **read + vocabulary** reference facade over `kg_read`/`kg_edges` + the ontology pack: compliance reads (`requirements_for`, `requirement_locations`, `curated_requirement_count`, `gated_pairs`, `policy_of_requirement`) on `ComplianceStore`; contract traversal + vocab (`party_counterparties`, `party_affiliates`, `contract_terms` — the `CONTRACTS_WITH`/counterparty naming over generic traversal, via DD-5); clause taxonomy (`canonical_clause_type`, `clause_type_vocabulary`) from the ontology `.ttl`; span/position assembly (`span_locations`). | R3b, AC-journey | todo | `capabilities/{contract_kg_store,compliance_store}.py` (+ a reference domain facade), `tests/capabilities/*` | each helper returns correct results hermetic + **live** on a real KG; no new domain import in the store |
| EP-REF-1c | Thin **compliance-leg invoker wrappers** as worked examples (`invoke_compliance_check`/`_document_check`/`invoke_policy_ingest` → `ainvoke_subgraph("compliance_*", …, resources=ws)`), showing the product the exact call shape. Reference-only; the product owns the FTC-routing/ad-compliance variants + guardrails. | R3b, AC-journey | todo | reference domain facade, `tests/*` | each wrapper runs a real compliance leg over a workspace end-to-end |

## R4 — Seam cleanup + product migration (ADR-0117 Part C).

| id | task | implements | status | files | verify |
|---|---|---|---|---|---|
| EP-SEAM-1 | Move the G-bucket seam functions behind the engine API (parse/ingest/QA/corpus-retrieval/generate/adapters/`ingest_corpus`; ship a generic bytes corpus adapter, retire `_ByteCorpus`). | R4 | todo | RuleWright `engine/seam.py`, `rag_wright/api/` | seam no longer reimplements orchestration |
| EP-SEAM-2 | Remove the I-bucket leaks from the product (13 `ArcadeDBStore.from_env`, raw-store-into-compliance, `query_embedder`, model-profile knowledge, id/format reimpls, the env-var bridge → `EngineConfig`). | R4, AC-no-leak | todo | RuleWright `engine/seam.py`, `resources.py` | import-linter: product imports no `rag_wright.store.*`/embedder/model ids |
| EP-SEAM-3 | Keep only the D-bucket in the seam (tenancy, scoping/`ScopeViolation`, compliance leg + FTC routing, clause/party vocabulary, citation-preview types, obs wiring, caching); split MIXED functions via DD-5. | R4 | todo | RuleWright `engine/seam.py` | seam = domain concerns only; both suites green |

## Deferred
- **ARD dynamic discovery seam** (search manifests by `representative_queries`) — not urgent; direct import of the API
  layer is the fast path.
