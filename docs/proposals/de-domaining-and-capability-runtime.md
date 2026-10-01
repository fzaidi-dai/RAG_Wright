# Proposal (DRAFT — for review): The engine platform — de-domaining the core + a capability runtime + the engine API layer

Status: **DRAFT for discussion** (not yet ADRs). Date: 2026-10-01. On approval this becomes **ADR-0068** (the engine
API layer + capability runtime) plus **`tasks.md` rows DD-1..6** (de-domaining, extending ADR-0067), and the seam
cleanup follows.

Companion: **`new-domain-developer-journey.md`** — the same design seen from a new-domain product developer's seat
(BidWright / LoomMatch). This doc is the engineering plan; that doc is the acceptance test ("could a textile or RFP dev
follow this smoothly?").

**Goal:** make building a new-domain product on the engine a matter of *config + a `.ttl` pack + domain capabilities +
a thin product seam* — by (A) de-domaining the core, (B) publishing a stable engine **API layer** + capability
runtime, and (C) cleaning the product seam of leaked engine concerns. Grounded against the code 2026-10-01.

---

## Part 0 — Settled design (decision log)

### 0.1 The execution model (corrected)

A **capability** is a registered unit with a **kind** (`function | subgraph | agent_skill | model | mcp_tool`), from
`EntryKind` (ard.py). Capabilities **compose** — they are not a flat "functions" layer:

```
composite capability (kind=subgraph = a compiled LangGraph StateGraph)   ← orchestration + hardening
   │  each graph node invokes a finer-grained capability:
   ├── function capability   (graph_query, generation, property_boosted_retrieval, …)
   ├── agent_skill capability (compliance_judgment, span_relevance_judgment, rlm_*)
   ├── model capability       (clause_function_classification; the property-classifier fleet)
   └── (by design) a component subgraph capability
          ▼
   leaf capabilities hit the store seam / LLM seam
```
Catalog today (registry.py:63-124): ~10 subgraph, ~20+ function, ~9 agent_skill, 1 model, 4 mcp_tool. LangGraph is the
substrate for subgraph-kind capabilities (retry/dead-letter/fan-out), **not** an agent loop. "Plain function" describes
only the `function`-kind leaves.

### 0.2 Decisions (locked this round)

- **D1 — The ARD registry stays metadata-only** (`name/kind/contract`); it never holds callables. ADR-0003 preserved.
  A capability "being registered" ≠ "callable through the registry."
- **D2 — Invocation = an engine *API layer* of generic, per-kind invokers**, name-driven, **not** per-capability
  public functions. Sync/async per kind: subgraph async-only (`ainvoke_subgraph`); function/model both. New capability
  ⇒ metadata entry + impl; the public API surface does **not** grow.
- **D3 — Internal graph composition uses direct import**; the per-kind invoker is the **product + MCP boundary
  surface** (not used for engine-internal node→capability calls, which stay direct for speed/typing).
- **Principle P — the engine API layer owns all non-domain cross-cutting concerns:** invocation + progress/monitoring
  + cost/usage (formalize the existing `usage_scope` + tracing seam) + resumability (later).
- **Internal resolver** (name→impl) lives *inside* the invocation layer, separate from ARD; impl choice —
  (a) convention/dynamic-import vs (b) a small dispatch table — deferred to implementation.
- **Invoker contract:**
  - **(a) per-kind hardening** — `ainvoke_subgraph` leans on the LangGraph scaffold (retry/dead-letter, already
    there) + wraps progress + usage; `invoke_function`/`invoke_model` add a **uniform light retry/timeout + automatic
    usage/progress** (functions/models have no hardening today — this is where they *gain* "safe & reliable"). Every
    invoker opens a `usage_scope` + a progress/trace span, so cost + progress are automatic for every kind.
  - **(b) `EngineResources` = an opaque `WorkspaceHandle`, engine-constructed from config** (NOT product-constructed
    from `ArcadeDBStore`). Passed to every invoker; the invoker injects what each kind needs.
- **Classifier granularity — one lane-level `<domain>_property_classification` `model` capability** (the whole
  dimension fleet behind one capability + its config), not per-dim. For the reference pack:
  `clause_property_classification`.

### 0.3 The config boundary — three kinds of things

1. **Hidden implementation** — ArcadeDB internals, vectors, connection, id formats. Behind the opaque handle.
2. **Engine-supported options (choices with defaults)** — a curated menu across quality/cost/latency/sovereignty:
   LLM by role/alias, embedder choice, reranker operating point, retrieval depth, chunking strategy, ingestion
   concurrency, judge on/off, structured-output method. **Defaults just work.** This *formalizes knobs that already
   exist* (model profiles, reranker operating point, retrieval args, the six ingest env-vars the seam leaks) into one
   `EngineConfig` surface.
3. **Pluggable BYO extensions** — only for a genuine gap the menu doesn't cover (e.g. an image embedder). A registered
   capability the engine loads, named like any option. Embedding and parsing must both be profile-backed +
   pluggable so a new choice/modality is additive, never an engine edit.

---

## Part A — De-domaining the core (extends ADR-0067)

Prerequisite for the engine API layer: a domain-agnostic resource handle/executor cannot sit over a CIK/contract-bound
store. Verified ADR-0067 landing state:

| Phase | Intent | State (2026-10-01) |
|---|---|---|
| P5a — typed-edge map → ttl | edge types/predicate IRIs from the pack ttl | ✅ landed (`load_typed_edges`) |
| P5b — node schema → pack | `ensure_schema` DDL from `load_kg_schema()` | ⚠️ partial — DDL pack-driven, but the store still imports contract contracts + carries ~30 domain methods |
| P5c — EntityResolver seam | pluggable resolver; generic `canonical_id`; drop CIK | ⚠️ partial — `Entity.canonical_id` landed; **no resolver interface**; `EntityId` **still CIK-hardcoded** |
| decision #5 — import-linter | engine must not name cik/edgar; Product→Engine | ❌ absent (no config, no CI workflow) |

**Backlog (each: TDD + no-behavior-change live A/B; order = finish P5b → P5c → taxonomy → enforcement):**

- **DD-1 — Lift domain methods off the engine store (finish P5b).** `ArcadeDBStore` carries ~30 contract methods
  (`upsert_contract`, `write_clause_kg`, `write_requirements`, `clause_typed_edges`, `exceptions_of_clause`,
  `spans_by_contract`, …) + imports `contracts.{compliance,property,contract_meta}`. Keep the generic
  `write_graph`/`graph_neighbors`/`hybrid_search` + pack-driven DDL + a generic **`kg_read`/`kg_write`**; move domain
  ops to a **capability-layer store extension** composing the generic store. Engine `store/` imports no contract
  contract. **Includes the compliance opaque-handle fix** (see Part B.3). *Cross-repo:* RuleWright's seam calls these
  store methods — coordinated change. **Biggest task.**
- **DD-2 — De-contract the generic `Span`/`Entity` columns.** `Span.contract_id` → `source_doc_id`; `Span.function` →
  pack-declared span label / generic `span_label`.
- **DD-3 — Extract the `EntityResolver` seam (P5c).** Injected `(mention clusters) → canonical ids`; EDGAR-CIK = one
  implementation (SEC pack default); generic default = the existing exact-normalized surface-form registry.
- **DD-4 — Genericize the `EntityId` contract (P5c; ask-first identifier).** Drop the 10-digit-CIK validator;
  `EntityId` = canonical-id string (FR-S.3 scheme unchanged: canonical id or `UNLINKED:` surrogate); CIK format → the
  SEC resolver.
- **DD-5 — Entity-type taxonomy → pack/`.ttl`.** `EntityType`/`RelationshipType` enums + the `graph_query`
  `CONTRACTS_WITH` default become pack-declared. This is what lets the MIXED seam functions split cleanly (generic
  traversal in the engine, the edge type + naming in the seam).
- **DD-6 — Enforcement (ADR-0067 #5).** Add `import-linter`: (i) engine must not reference `cik`/`edgar`/the contract
  pack once DD-1..5 land; (ii) Product→Engine. Decide where CI runs (no `.github/workflows/` exists yet).

---

## Part B — ADR-0068 sketch: the engine API layer + capability runtime

Depends on Part A. The engine publishes a **stable API layer** (the thing the product imports) with these pillars.

### B.1 The engine API surface

- **Workspace / resources:** `open_workspace(config, *, corpus) -> WorkspaceHandle` (opaque; resolves + caches
  store/embedder/schema; absorbs `resources.py`'s per-customer caches). `EngineConfig` carries store backend +
  connection, model aliases, embedding profile(s), and the **`options` catalog** (defaulted). The handle **is** the
  `EngineResources` of decision (b) — engine-constructed, never a store the product builds.
- **Per-kind invokers** (D2): `ainvoke_subgraph(name, inputs, resources)`, `invoke_function`/`ainvoke_function`,
  `invoke_model`/`ainvoke_model`, agent-skill invoker. Name-driven; internal name→impl resolver.
- **Generic KG access:** `kg_read(ws, node_type, *, where=…)` / `kg_write(...)` — the generic mechanism behind the
  seam's `requirements_for`/`span_locations`/etc.
- **Id/format accessors:** `document_of(span)`, span→location/bbox, requirement→policy — so the product never parses
  engine id strings (today reimplemented as `decode_bbox`/`document_of`/`policy_of_requirement` in the seam).
- **Options as choices, not implementations:** embedder + model by alias; reranker/retrieval/chunking/ingest knobs via
  `EngineConfig.options` (formalizing scattered args + env-var names); embedding/parser profile-backed + pluggable.
- **Cross-cutting utilities (Principle P):** usage/cost reporting (`usage_scope`), progress/monitoring, tracing —
  formalized into the API, not internal helpers; resumability later.

### B.2 Capability runtime

- **P1 — the API layer above** is the invocation surface (importable; no ARD needed to start; ARD only adds
  discovery, deferred).
- **P2 — register every capability** with `name/kind/contract` (metadata only, D1) + an impl — **including a
  lane-level `clause_property_classification` `model` capability** (the 29-dim fleet), so the classifier is invokable
  from graphs (direct import internally) *and* from products (`invoke_model("clause_property_classification", …)`).
- **P3 — generic capability→MCP adapter:** one shim that exposes any registered capability as an MCP tool via
  `ainvoke_<kind>(name, …)`, retiring the bespoke per-subgraph servers.
- **P4 — per-kind capability-authoring skills** (subgraph/function/model/agent_skill) + the registration + ARD +
  invocation contract, so "implement a capability of kind X" is spec-driven and skill-guided (the engine repo already
  has CLAUDE.md + `docs/playbook.md`; add the per-kind skills).
- **P5 — execution-model enrichment (later):** resumability/interruptibility via the LangGraph **checkpointer**
  (store-backed) + `interrupt`/resume + a progress contract. Currently unused → "turn on + standardize."
- **Deferred:** ARD dynamic discovery seam (not urgent; direct import of the API layer is the fast path).

### B.3 Worked example — the compliance opaque-handle redesign

The hardest seam case; it defines the handle for everyone.

- **Today (leak):** `build_compliance_store(...) -> ArcadeDBStore` is handed to the product and passed into **8
  functions**; the product also builds `query_embedder()` and resolves model profiles.
- **Target:** `ws = engine.open_workspace(cfg, corpus="compliance")` (opaque) →
  `await engine.ainvoke_subgraph("compliance_check", {subject_text|subject_doc, sources, k}, resources=ws)`.
- **Collapse:** `build_compliance_store` → `open_workspace`; `invoke_policy_ingest` →
  `ainvoke_subgraph("compliance_ingestion", …, resources=ws)`; the 4 check variants → `compliance_check`
  (subject-type is an **input**, not separate functions) + `ad_compliance_check` (a **distinct domain capability** —
  FTC claim extraction); the 3 reads → `kg_read(ws, "Requirement", where=source)` with a thin pack helper.
- **Generic/domain split:** generic `compliance_check` (subject vs a typed Requirement KG) = **reference-pack**
  capability built on engine-core `kg_read` + invokers (engine-core stays free of the "requirement" concept); the
  FTC-ad variant = domain capability; `UnknownPolicy` = product mapping of the engine's typed
  `UnknownComplianceSourceError`.
- **Decisions locked:** (1) subject-type as input, not separate capabilities; (2) generic `compliance_check` is
  reference-pack, not engine-core.

---

## Part C — Seam cleanup (falls out of A + B)

From the seam inventory, three buckets:

- **G — generic → move into the engine API** (they exist only because the engine lacked public entrypoints): parse,
  `build_source_document`, ingest invoke + streaming, build/invoke QA, build/invoke corpus retrieval (minus scope
  policy), `generate_answer`, the shape adapters, bulk `ingest_corpus`, and `_ByteCorpus` (the engine should ship a
  generic bytes corpus adapter, not GCS-only).
- **I — infra the product must not know → hide behind the API:** the 13 `ArcadeDBStore.from_env` sites, the raw-store
  passed into 8 compliance functions, `query_embedder()` construction, model-profile knowledge
  (`ENGINE_DEFAULT_CONSTRAINT_MODEL`, `model_for`, extraction-model labels), and the engine id/format reimplementations
  (`decode_bbox`/`document_of`/`policy_of_requirement`). The env-var-name bridge (`export_engine_env`) → a typed
  `EngineConfig`.
- **D — genuinely product → stays in the seam:** tenancy/workspace routing, scoping + `ScopeViolation`, the compliance
  domain leg + FTC routing, the clause/party **vocabulary**, citation-preview types, observability wiring, caching
  policy.

**MIXED functions** (e.g. `party_counterparties` = generic 1-hop typed traversal + `CONTRACTS_WITH` + "counterparty")
split cleanly once DD-5 lands: engine exposes the parameterized mechanism; the seam supplies the domain parameter +
name. **RuleWright migration:** move its seam onto `open_workspace` + the invokers (keep direct import working during
transition; the invoker is the sanctioned boundary).

---

## Sequence & next steps

1. **De-domain the core (A / DD-1..6)** — one task at a time, TDD + live A/B, approval gate; DD-1 carries the
   RuleWright coordination and the compliance opaque-handle fix.
2. **Publish the engine API layer (B.1)** — `open_workspace`/handle + `EngineConfig`+options, per-kind invokers,
   `kg_read`, id/format accessors, embedding/model options, formalized usage/progress.
3. **Capability runtime (B.2)** — register every capability incl. the lane-level classifier; generic capability→MCP;
   per-kind authoring skills.
4. **Resumability (B.5)** — LangGraph checkpointer, later.
5. **ARD discovery** — later, not urgent.
6. **Seam cleanup (C)** + the developer-journey doc as the acceptance test.

On approval: write **ADR-0068**, add **DD-1..6** to `tasks.md` (ADR-0067-continuation), and begin DD-1.

## CLS arc status (done, committed `6d4dfc4`)
Classifier-first Step-3a (ADR-0115/0116) is live-validated on the full 29-dim fleet
(`tests/spans/test_dim_fleet_live.py`): new dims fire on-function with correct values, all abstain off-function, no
regression; the 2 data-starved dims (collateral_type, escrow_release_trigger) correctly abstain. Remaining CLS item:
source data for the starved rare values (future enhancement). The 29-dim fleet is what becomes the lane-level
`clause_property_classification` capability in B.2.
