# Tasks: The Engine Platform Boundary

Child task ledger for `./SPEC.md` (child of the root `SPEC.md`/`tasks.md`). Governs ADR-0117 + the ADR-0067
continuation. Runs under the main working loop (CLAUDE.md): one task, contract-first TDD + a live A/B or smoke test,
approval gate, atomic commit. Statuses: `todo | in-progress | awaiting-approval | done`.

> **RESUME / NEXT UP (2026-10-02):** **DD-1a + DD-1b DONE** (generic `kg_read`/`kg_write`; the clause-KG, flat-graph,
> contract-meta + requirement WRITES relocated to `ContractKGStore`/`ComplianceStore`; `store/arcadedb.py` imports no
> `contracts.{compliance,contract_meta,property}`; guard green; full suite 1643 pass + 15 live store tests).
> **Next up: DD-1c** — the edge-TRAVERSAL reads (`clause_typed_edges`/`contract_clause_kg`/`exceptions_of_clause`/
> `span_properties`/`clauses_with_property`) via a generic `kg_edges` traversal primitive + the remaining node reads;
> move `patch_canonical_jurisdictions` (drops the local `contracts.jurisdiction` import) → full "no domain-contract
> import" guard. Then DD-2..6. Order: R1 → R2 (EP-API-*) → R3 (EP-RT-*) → R4 (EP-SEAM-*) → R5. Design: `docs/proposals/*`.
> (Compliance opaque-handle migration lands with EP-API-1; cross-repo RuleWright via handoffs — not a factor here.)

## R1 — De-domain the core (extends ADR-0067; prerequisite). Each: TDD + no-behavior-change live A/B.

| id | task | implements | status | files | verify |
|---|---|---|---|---|---|
| DD-1a | Add the generic, backend-agnostic typed-node read **`Store.kg_read`** (equality/`IN` filters, `fields`/`distinct`/`order_by`/`limit`, empty-list→`[]`) + impl; re-express `all_requirements`/`contract_by_id`/`spans_by_contract` onto it with SQL-parity. Proves `kg_read` adequate before DD-1b's migration. | R1, ADR-0117 | **done (2026-10-02)** | `store/seam.py`, `store/arcadedb.py`, `tests/store/test_kg_read.py`, `tests/store/test_arcadedb_schema.py` (stub) | 7 contract + 3 parity tests green; full suite 1632 pass, 0 fail |
| DD-1b | Add generic **`kg_write`** (typed node + edge upsert, schema-driven encoding via `_property_types`); re-express the WRITES (`write_clause_kg`/`write_property_graph`/`upsert_contract`/`write_requirements` + `_clause_kg_statements`) into capability-layer **`ContractKGStore`** + **`ComplianceStore`** extensions; re-key `_TYPED_DIMENSION_EDGE` off the str map (drops `PropertyDimension`); **remove `contracts.{compliance,contract_meta,property}` imports from `store/arcadedb.py`**; switch callers (pipeline + `compliance_ingestion` write-seam + 5 scripts); extend the domain-neutral guard. | R1, ADR-0067 P5b, AC-no-leak | **done (2026-10-02)** | `store/{arcadedb,seam}.py`, `subgraphs/{contract_ingestion_pipeline,compliance_ingestion}.py`, new `capabilities/{contract_kg_store,compliance_store}.py`, 5 scripts, `tests/*` | `kg_write` 8 tests + statement parity + guard green; **15 live store tests** (real ArcadeDB) pass; full suite 1643 pass |
| DD-1c | The edge-TRAVERSAL reads (`clause_typed_edges`/`contract_clause_kg`/`exceptions_of_clause`/`span_properties`/`clauses_with_property`) via a generic **`kg_edges`** traversal primitive + the remaining pure node reads → the extensions; move `patch_canonical_jurisdictions` (drops the local `contracts.jurisdiction` import). Extend the guard to the FULL "no domain-contract import." | R1, AC-no-leak | todo | `store/arcadedb.py`, `store/seam.py`, `capabilities/contract_kg_store.py`, callers, `tests/store/*` | engine `store/` imports no domain contract at all; traversal reads identical live; guard green |
| DD-2 | De-contract the generic infra columns: `Span.contract_id` → `source_doc_id`; `Span.function` → pack-declared span label / generic `span_label`. | R1 | todo | `store/arcadedb.py` DDL + readers/writers | identical live schema + span round-trip |
| DD-3 | Extract the `EntityResolver` seam (injected `(mention clusters) → canonical ids`); EDGAR-CIK = one impl (SEC pack default); generic default = the exact-normalized surface-form registry. | R1, ADR-0067 P5c | todo | `capabilities/entity_resolution.py`, `store/seam.py`, pipeline resolve/write wiring | SEC corpus resolves identically with CIK resolver injected; non-SEC doc resolves via surface-form default |
| DD-4 | Genericize the `EntityId` contract: drop the 10-digit-CIK validator; `EntityId` = canonical-id string (FR-S.3 scheme unchanged); CIK format → the SEC resolver. **Ask-first identifier — flagged.** | R1, ADR-0067 P5c, FR-S.3 | todo | `contracts/identifiers.py` + consumers | identifiers round-trip; no fabricated ids |
| DD-5 | Entity/edge taxonomy → pack `.ttl`: `EntityType`/`RelationshipType` enums + `graph_query`'s `CONTRACTS_WITH` default become pack-declared (enables the MIXED-seam split). | R1, ADR-0066 | todo | `contracts/ontology.py`, `graph_query.py`, `graph_extraction.py`, `entity_resolution.py` | contract taxonomy identical when the contract pack is loaded |
| DD-6 | Enforcement: add `import-linter` — (i) engine must not reference `cik`/`edgar`/the contract pack; (ii) Product→Engine. Decide where CI runs (no `.github/workflows/` yet). | R1, AC-no-leak, ADR-0067 #5 | todo | `pyproject.toml`/`.importlinter`, CI config | a deliberate violating import fails the linter |

## R2 — Engine API layer (ADR-0117). Each: contract-first TDD + a live smoke test.

| id | task | implements | status | files (proposed) | verify |
|---|---|---|---|---|---|
| EP-API-1 | `open_workspace(config, *, corpus) -> WorkspaceHandle` (opaque; resolves+caches store/embedder/schema) + `EngineConfig` (store backend, model aliases, embedding profile, `options` catalog with defaults). Absorbs the product's per-customer resource caches. | R2 | todo | new `rag_wright/api/` (workspace, config) | product opens a workspace and runs a capability without importing `ArcadeDBStore`/`query_embedder`; `ws` exposes no store |
| EP-API-2 | Per-kind invokers: `ainvoke_subgraph` / `invoke_function`+`ainvoke_function` / `invoke_model`+`ainvoke_model` / agent-skill invoker, name-driven; internal `name→impl` resolver (convention or dispatch table); per-kind hardening; every invoker opens `usage_scope` + progress/trace span. | R2, AC-runtime | todo | `rag_wright/api/invoke.py` | invoke each kind by name; functions/models gain uniform retry/timeout + automatic usage/progress |
| EP-API-3 | Generic `kg_read(ws, node_type, *, where=…)` / `kg_write` + id/format accessors (`document_of`, span→location/bbox, requirement→policy) so the product never parses engine id strings. | R2 | todo | `rag_wright/api/kg.py`, `rag_wright/api/ids.py` | seam's `requirements_for`/`span_locations`/id-splits reproduced via the API |
| EP-API-4 | Embedding + model as **options** (by alias/profile) + pluggable embedder/parser capabilities; formalize the `options` catalog (reranker/retrieval/chunking/ingest knobs) with defaults, replacing the six leaked ingest env-vars. | R2 | todo | `rag_wright/api/config.py`, model/embedding profile seams | a non-default embedder/model/knob is selected via config; defaults unchanged behavior |

## R3 — Capability runtime (ADR-0117). 

| id | task | implements | status | files (proposed) | verify |
|---|---|---|---|---|---|
| EP-RT-1 | Register every capability (metadata + impl), **including a lane-level `clause_property_classification` `model` capability** (the 29-dim fleet) invokable from graphs (direct import) and products (`invoke_model(...)`). | R3, AC-runtime, ADR-0115/0116 | todo | `spans/` + `capabilities/registry.py` + `rag_wright/api/` | the classifier fleet invokes by name from a product; catalog complete |
| EP-RT-2 | Generic capability→MCP adapter: one shim exposing any registered capability as an MCP tool via `ainvoke_<kind>(name, …)`; retire the bespoke per-subgraph servers. | R3, AC-runtime | todo | `rag_wright/mcp/` | any registered capability served as an MCP tool with no bespoke server |
| EP-RT-3 | Per-kind capability-authoring skills (subgraph/function/model/agent_skill) + the registration+ARD+invocation contract; wire into CLAUDE.md/playbook. | R3 | todo | `.claude/skills/`, `docs/playbook.md` | a new capability of each kind can be authored by following the skill |
| EP-RT-4 | **(later)** Resumability/interruptibility via the LangGraph checkpointer (store-backed) + `interrupt`/resume + a progress contract. | R5 | todo | `subgraphs/scaffold.py`, `rag_wright/api/` | a long run resumes after interruption with no recompute of completed nodes |

## R4 — Seam cleanup + product migration (ADR-0117 Part C).

| id | task | implements | status | files | verify |
|---|---|---|---|---|---|
| EP-SEAM-1 | Move the G-bucket seam functions behind the engine API (parse/ingest/QA/corpus-retrieval/generate/adapters/`ingest_corpus`; ship a generic bytes corpus adapter, retire `_ByteCorpus`). | R4 | todo | RuleWright `engine/seam.py`, `rag_wright/api/` | seam no longer reimplements orchestration |
| EP-SEAM-2 | Remove the I-bucket leaks from the product (13 `ArcadeDBStore.from_env`, raw-store-into-compliance, `query_embedder`, model-profile knowledge, id/format reimpls, the env-var bridge → `EngineConfig`). | R4, AC-no-leak | todo | RuleWright `engine/seam.py`, `resources.py` | import-linter: product imports no `rag_wright.store.*`/embedder/model ids |
| EP-SEAM-3 | Keep only the D-bucket in the seam (tenancy, scoping/`ScopeViolation`, compliance leg + FTC routing, clause/party vocabulary, citation-preview types, obs wiring, caching); split MIXED functions via DD-5. | R4 | todo | RuleWright `engine/seam.py` | seam = domain concerns only; both suites green |

## Deferred
- **ARD dynamic discovery seam** (search manifests by `representative_queries`) — not urgent; direct import of the API
  layer is the fast path.
