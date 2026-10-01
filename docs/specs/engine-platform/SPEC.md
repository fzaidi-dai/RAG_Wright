# Child Spec: The Engine Platform Boundary (engine API layer + capability runtime + de-domaining)

Version 0.1 · 2026-10-02 · **Child spec of `SPEC.md` v0.1** (the engine capability spec). Status: **active workstream.**

This is a focused, self-contained spec for one architectural workstream, carved out of the main `SPEC.md`/`tasks.md`
to keep it reviewable (the root `tasks.md` is ~2,300 lines). It traces to the parent spec's FR-C/FR-S set and
boundaries, and to **ADR-0052** (engine/product split), **ADR-0066** (knowledge in the ontology), **ADR-0067**
(de-domaining the KG/entity layer), and **ADR-0117** (the engine API layer + capability runtime — the governing
decision). Design detail is in `docs/proposals/de-domaining-and-capability-runtime.md` and
`docs/proposals/new-domain-developer-journey.md`; this spec states *what must be true when done*, and its tasks live in
`./TASKS.md`.

## 1. Objective

Make building a **new-domain product** on the engine a matter of **config + a `.ttl` pack + domain capabilities + a
thin product seam** — by publishing a stable, domain-agnostic **engine API layer** and a **capability runtime** over a
**de-domained core**. The acceptance test is `new-domain-developer-journey.md`: a developer who has never seen the
contract/compliance domain can follow the journey smoothly.

## 2. Context (grounded, the problem)

The engine does the hard parts but doesn't present them as an API, so the product repo reimplements and **leaks engine
concerns** (13 `ArcadeDBStore.from_env` sites; a raw store threaded through the whole compliance leg; `query_embedder`
built in the product; model-profile knowledge + a pinned model id; engine id/format reimplemented). Separately,
contract/compliance domain is embedded in core-presenting modules (`EntityId`=CIK; the store's ~30 domain methods;
hardcoded entity/edge enums). The boundary is leaky **by omission**. See ADR-0117 Context.

## 3. Decisions in force (from ADR-0117; do not re-litigate)

- Capability = a registered unit with a `kind`; capabilities compose; subgraph-kind = compiled LangGraph.
- ARD registry stays metadata-only (no callables).
- Invocation = per-kind, name-driven invokers in an engine **API layer** (not per-capability public functions);
  internal `name→impl` resolver.
- Per-kind hardening; automatic usage/progress on every invoker.
- Resources = an opaque `WorkspaceHandle`, engine-constructed from `EngineConfig`.
- Internal composition = direct import; the invoker is the product + MCP boundary.
- Config boundary = hidden impl / engine-supported options-with-defaults / pluggable BYO; embedding + parsing are
  profile-backed + pluggable.
- Capability runtime: register every capability (incl. a lane-level `clause_property_classification` model
  capability); generic capability→MCP; per-kind authoring skills. ARD discovery deferred.
- De-domaining the core is a prerequisite.

## 4. Requirements (what must be true when done)

**R1 — De-domained core (prereq; extends ADR-0067).** Engine `store/`, `contracts/identifiers.py`, and the entity-KG
capabilities carry no contract/CUAD/SEC assumption: no domain methods on the generic store, generic `EntityId`, a
pluggable `EntityResolver`, pack-declared entity/edge taxonomy, de-contracted `Span`/`Entity` columns; an import-linter
guards it.

**R2 — Engine API layer.** A stable public surface: `open_workspace(config, corpus) -> WorkspaceHandle` (opaque) +
`EngineConfig` (store backend, model aliases, embedding profile, `options` catalog with defaults); the per-kind
invokers; `kg_read`/`kg_write`; id/format accessors; embedding/model as options + pluggable; formalized usage/cost +
progress + tracing. The product never touches `ArcadeDBStore`/`query_embedder`/model ids/id formats.

**R3 — Capability runtime.** Every capability registered (metadata + impl), including the lane-level
`clause_property_classification` model capability; a generic capability→MCP adapter; per-kind capability-authoring
skills. Direct import of the API layer is the fast path; ARD discovery deferred.

**R4 — Seam cleanup + product migration.** The generic (G) and infra (I) seam functions move behind the API; the
product seam keeps only domain (D) concerns; RuleWright migrates onto `open_workspace` + the invokers.

**R5 — Resumability (later).** Durable/interruptible/resumable execution via the LangGraph checkpointer + a progress
contract — a later increment, after R2/R3.

## 5. Acceptance criteria

- **AC-journey:** the steps in `new-domain-developer-journey.md` are all backed by real API (a new domain = config +
  `.ttl` + capabilities + seam), demonstrated by a minimal non-contract smoke domain.
- **AC-no-leak:** an import-linter rule proves the product (and a new-domain seam) need not import
  `rag_wright.store.*`, the embedder, or model ids; the engine references no `cik`/`edgar`.
- **AC-parity:** every de-domaining/seam refactor passes a no-behavior-change live A/B; new API is TDD + a live smoke
  test.
- **AC-runtime:** any registered capability can be invoked by name via its per-kind invoker and exposed as an MCP tool
  through the generic adapter; the classifier fleet is invokable as `clause_property_classification` from a graph and a
  product.

## 6. Phased plan

See **`./TASKS.md`**. Order: **R1 de-domaining (DD-1..6) → R2 API layer → R3 capability runtime → R4 seam cleanup →
R5 resumability.** Each task runs under the main working loop (CLAUDE.md): one task, TDD/contract-first + a live A/B or
smoke test, approval gate, atomic commit.
