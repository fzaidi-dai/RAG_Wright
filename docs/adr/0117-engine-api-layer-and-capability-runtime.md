# ADR-0117: The engine API layer + capability runtime (the engine-as-platform boundary)

Date: 2026-10-02
Status: **Accepted** (design approved 2026-10-02; implementation phased + gated via the child spec
`docs/specs/engine-platform/`). Supersedes nothing; extends ADR-0003 (ARD registration), ADR-0052 (engine/product
split), ADR-0066 (knowledge in the ontology), ADR-0067 (de-domaining the KG/entity layer).

Design detail lives in `docs/proposals/de-domaining-and-capability-runtime.md` (engineering plan) and
`docs/proposals/new-domain-developer-journey.md` (the acceptance test — a new-domain dev's journey, BidWright/RFP +
LoomMatch/textile). This ADR records the decision; the child spec + tasks carry the build.

## Context

The engine already does the hard parts (parse/chunk/segment/embed/search/rerank/graph/resolve; compiled LangGraph
pipelines; the model seam; usage/tracing), but it does **not present them as a stable API**, so the product repo
(RuleWright) reimplements and leaks engine concerns: it imports `ArcadeDBStore` in 13 places (and is handed a **raw
store** across the whole compliance leg — 8 functions), constructs `query_embedder()` directly, holds model-profile
knowledge and a pinned model id, and reimplements engine id/formats (`decode_bbox`/`document_of`/
`policy_of_requirement`). Separately, contract/compliance domain is still embedded in core-presenting modules
(`EntityId`=CIK, the store's ~30 domain methods, hardcoded entity/edge enums). The boundary is leaky **by omission**,
not by design: the engine never published the API that would avoid it.

A new-domain product (RFP, textile, …) should be *config + a `.ttl` pack + domain capabilities + a thin seam*. Today
it can't be, because (a) the core carries contract/CIK assumptions and (b) there is no stable engine API to invoke
capabilities or open storage without touching ArcadeDB/BGE.

## Decision

Publish a stable, domain-agnostic **engine API layer** and a **capability runtime** over it, on top of a de-domained
core.

1. **A capability is a registered unit with a `kind`** (`function | subgraph | agent_skill | model | mcp_tool`).
   Capabilities **compose** (a subgraph-kind capability's LangGraph nodes invoke finer-grained capabilities, down to
   leaves that hit the store/LLM seams). "Plain function" is only the `function`-kind leaves.

2. **The ARD registry stays metadata-only** (`name/kind/contract`); it never holds callables (ADR-0003 preserved).
   Registration ≠ callable-through-the-registry.

3. **Invocation = an engine API layer of generic, per-kind invokers** — `ainvoke_subgraph(name, inputs, resources)`,
   `invoke_function`/`ainvoke_function`, `invoke_model`/`ainvoke_model`, an agent-skill invoker — **name-driven, not
   per-capability public functions** (so the public surface does not grow with the catalog). Subgraph is async-only;
   function/model are sync+async. An engine-internal `name → impl` resolver sits inside the invocation layer (separate
   from ARD).

4. **Per-kind hardening:** `ainvoke_subgraph` leans on the LangGraph scaffold's retry/dead-letter + wraps
   progress/usage; `invoke_function`/`invoke_model` add a uniform light retry/timeout + automatic usage/progress.
   Every invoker opens a `usage_scope` + progress/trace span, so cost + progress are automatic for every kind.

5. **Resources = an opaque `WorkspaceHandle`, engine-constructed from `EngineConfig`** (never a store the product
   builds). `open_workspace(config, *, corpus) -> WorkspaceHandle` resolves + caches store/embedder/schema internally;
   the product never imports `ArcadeDBStore`/`query_embedder` and `ws` has no `.store`. This absorbs the product's
   per-customer resource caches.

6. **Internal composition uses direct import; the per-kind invoker is the product + MCP boundary.**

7. **The config boundary has three kinds of thing:** hidden implementation (behind the handle); engine-supported
   **options with defaults** (model-by-alias, embedder choice, reranker/retrieval/chunking/ingest knobs, judge on/off
   — formalizing scattered args + the six ingest env-vars the seam leaks); and pluggable BYO extensions (a custom
   embedder/parser the engine doesn't ship). Embedding and parsing are profile-backed + pluggable so a new choice or
   modality is additive, never an engine edit.

8. **The engine API layer also owns the non-domain cross-cutting surface:** `kg_read`/`kg_write`, id/format accessors
   (so the product never parses engine id strings), usage/cost + progress + tracing (formalizing `usage_scope` + the
   tracing seam), and resumability (later, via the LangGraph checkpointer).

9. **Capability runtime:** register every capability (metadata + impl), **including a lane-level
   `clause_property_classification` `model` capability** (the 29-dim fleet) invokable from graphs and products; a
   **generic capability→MCP adapter** (retiring the bespoke per-subgraph servers); and **per-kind
   capability-authoring skills** so "implement a capability of kind X" is spec-driven. **ARD dynamic discovery is
   deferred** (direct import of the API layer is the fast path; discovery only adds a name-finding convenience).

10. **De-domaining the core is a hard prerequisite** (ADR-0067 / the DD tasks): the opaque handle and the generic
    invokers cannot be domain-agnostic over a store that hardcodes CIK/contract concepts.

## Consequences

- A new domain becomes **config + a `.ttl` pack + domain capabilities + a thin seam** (the developer-journey doc is
  the acceptance test). The generic ~70% is the engine; the domain ~30% is the product.
- The product seam sheds its leaked infra (store/embedder/model/id-format) and keeps only real product concerns
  (tenancy, scoping, use-case orchestration, domain vocabulary, obs wiring, auth). **RuleWright migrates** onto
  `open_workspace` + the invokers (direct import kept working during transition).
- Large, phased, gated — tracked in `docs/specs/engine-platform/TASKS.md`. Refactors carry a no-behavior-change live
  A/B; new API is TDD + a live smoke test per the working loop.
- ADR-0003's metadata-only registry stands; this ADR adds the *invocation* layer beside it, not inside it.

## References
- Proposals: `docs/proposals/de-domaining-and-capability-runtime.md`, `docs/proposals/new-domain-developer-journey.md`
- Child spec + tasks: `docs/specs/engine-platform/SPEC.md`, `docs/specs/engine-platform/TASKS.md`
- Extends: ADR-0003, ADR-0052, ADR-0066, ADR-0067. Related: ADR-0115/0116 (the 29-dim classifier → the lane-level
  `clause_property_classification` capability).
