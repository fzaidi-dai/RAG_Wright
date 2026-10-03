# ADR-0118: Engine core API vs ARD — generic primitives are core, the invoker is an adapter-free `impl_ref` ARD client

Date: 2026-10-03
Status: **Accepted** (design approved 2026-10-03). Extends ADR-0003 (ARD registration is metadata, no callables),
ADR-0052 (engine/product split), ADR-0066 (knowledge in the ontology), ADR-0117 (engine API layer + capability
runtime). Sharpens ADR-0117's invoker: supersedes its interim central adapter-dict binding.

## Context

ADR-0117 published the engine API + a capability invoker implemented as a **progressive-loading ARD client** (a
light metadata index built from the manifest specs, with the capability implementation imported lazily on first
invoke). That part stands. But two things in the current state are wrong for a domain-agnostic engine that products
build on:

1. **Generic primitives are registered in ARD.** `hybrid_search`, `graph_query`, `fusion`, `embedding`, `parsing`,
   `reranking`, `semantic_chunking`, the retrieval-core functions — these are *building blocks*, not agent/product-
   facing discoverable capabilities. They are consumed by **direct import**: by the engine's own orchestration
   graphs, and by a product through the engine's core API. No orchestrator needs to *discover* "fusion" at runtime
   and call it standalone. Registering them in ARD is wrong-layer, and it means the engine would **ship a
   pre-populated ARD registry** that pollutes a new domain's registry with the contract/compliance reference pack.

2. **The invoker's `name → implementation` binding is a central, hand-written adapter dict** (`api/invoke.py`
   `_SUBGRAPH_ADAPTERS`; model adapters in `spans/model_capabilities.py`). A central list the engine hand-maintains
   is exactly what a developer-populated registry must not require: a developer who authors and registers a new
   domain capability cannot be expected to edit a central engine dict to make it invocable.

The purpose of the exercise is a strong, generic engine on which reliable retrieval **products** are built. ARD's
job is **discovery of the domain capabilities a developer/product builds** (graphs, domain functions, models, skills,
MCP tools), driven by that product's `.ttl`/KG. The engine's job is to ship generic **primitives + mechanisms** and
to invoke capabilities reliably — not to pre-own a domain's registry.

## Decision

**1. Two distinct layers, cleanly separated.**

- **Engine core API** (code; imported directly; versioned; documented): the generic **primitives** (`hybrid_search`,
  `graph_query`, `fusion`, `embedding`, `parsing`, `reranking`, `semantic_chunking`, retrieval-core, …) and the
  **mechanisms** (`open_workspace`, the invokers, `kg_read`/`kg_write`, `measure_usage`, `parse_document`, the
  LangGraph scaffold with retry/dead-letter/interrupt/resume, the model + embedding seams). These are **not in ARD**.
  Ontology-driven ingestion/population graphs remain **core** (they are the engine's `.ttl`-driven machinery, not a
  developer-registered capability).

- **ARD registry**: holds only **agent/product-facing capabilities** — the domain graphs, domain functions, models,
  skills, and MCP tools a developer builds. The engine **ships no pre-populated generic-primitive registry**; the
  contract/compliance reference pack becomes **example registration code a developer can run**, not shipped registry
  state.

**2. Generic ≠ a thing to promote; generic = a thing to DE-REGISTER from ARD.** A primitive that is only consumed by
direct import stays an ordinary function in `capabilities/` (a helper), **removed from the ARD manifests**. It is
promoted to the documented public core surface only if a product actually needs to call it directly; otherwise it
stays an internal helper. No sweeping "make everything a public primitive" exercise — case-by-case, grounded.

**3. A generic capability reaches agents only via MCP.** If an agent ever needs to discover/invoke a generic engine
primitive cross-process, expose it as an **MCP tool** (the generic capability→MCP adapter, ADR-0117/EP-RT-2) — never
by re-registering it as an ARD Python-invoker capability. Direct engine-API calls are the default; MCP is the opt-in
agent surface.

**4. The invoker is an adapter-free ARD client that resolves `impl_ref` lazily.** The central hand-written adapter
dict is removed. Each capability's **registration carries an `impl_ref`** — a vendor-extension pointer to its
implementation factory (`"package.module:factory"`) — plus the small, declared input contract the factory needs. The
client:
   - holds the **light index** (name + minimal metadata: kind, description, `impl_ref`, input contract) at startup —
     no implementation imported (progressive loading, unchanged in spirit);
   - on invoke, **imports the `impl_ref` factory lazily**, builds it over the opaque `WorkspaceHandle` (store, model
     ids, embedder — resources the capability declares it needs), invokes it, and wraps the call in a trace span;
     usage/cost is the caller's `measure_usage()` scope (EP-API-5).

   `impl_ref` is a **vendor extension**, consistent with ADR-0003: the ARD **manifest/registry still stores no
   callable** — it stores a *string pointer*; the client does the import. This keeps ARD metadata-only while removing
   the central engine-owned adapter list, so a developer registering a capability (with its `impl_ref`) makes it
   invocable **without editing engine code**.

## Consequences

- The engine becomes genuinely domain-agnostic at the registry level: a new domain's ARD contains only that domain's
  capabilities; the engine ships primitives + mechanisms + an empty capability registry.
- A developer authors a capability, registers it with an `impl_ref`, and it is immediately invocable by name and
  (optionally) exposable via MCP — zero central engine edits. This is the "developer populates ARD" model.
- The invoker's drift guard changes shape: instead of "every central adapter ∈ catalog," the client validates that a
  registered `impl_ref` resolves to a callable of the declared kind; the authoring-contract guardrail is updated
  accordingly.
- `graph_query` is already de-contracted (DD-5a). `property_boosted_retrieval` and `query_constraint_extraction` must
  be de-coupled from the clause vocab (pass as args / config / `.ttl`) as part of de-registering/generalizing the
  primitives.
- Reference-pack registrations (contract/compliance capabilities + the classifier models) move to example/setup code;
  the engine no longer publishes them as shipped registry state. Handoff doc + ledger follow.

## Migration (sequenced; each a gated task with tests + a live check where applicable)

1. **De-register the generic primitives from ARD** (remove from the manifests/`_SPECS`), keeping them as
   `capabilities/` helpers; de-couple `property_boosted_retrieval` + `query_constraint_extraction` from the clause
   vocab. (Also decide, case-by-case, the few promoted to the documented public core surface.)
2. **Add `impl_ref` to the capability registration** (vendor extension) + the light-index carrier; make the invoker
   resolve `impl_ref` lazily and **delete the central adapter dicts**. Update the drift guard / authoring-contract
   test. This is the load-bearing increment — done on its own, carefully tested (hermetic + the live EP-E2E path).
3. **Engine ships no pre-populated generic-primitive registry**; move reference-pack registrations to example setup.
4. Update the authoring skill, handoff doc, and ledger to the adapter-free `impl_ref` model.

Step 2 is not combined with step 1 in one change: the de-registration and the invoker rework are each verified
independently.
