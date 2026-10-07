---
name: authoring-a-capability
description: >-
  How to author a new RAG_Wright engine capability of any kind (subgraph, function, model, agent_skill, mcp_tool)
  so it is registered, ARD-discoverable, and invokable by name through the engine API. Use it whenever you add a
  new capability or a new-domain product/graph needs one: it gives the shared registration + ARD + invocation
  contract (the four surfaces + the definition of done), the per-kind implementation specifics, and the
  conformance guardrail that keeps the catalog honest. Grounded against the real code; keep it in step with it.
---

# Authoring a capability

A **capability** is a named, ARD-registered unit of engine behavior (FR-C). Every capability has a `kind`
(`ard.py::EntryKind`): `subgraph | function | model | agent_skill | mcp_tool` (`dagster_asset` is reserved). The
contract below is the SAME for every kind; only the implementation differs. Capabilities compose — a subgraph calls
functions/models; a product invokes a capability by name through `rag_wright.api`.

**Ground every call before writing it** (CLAUDE.md library rule). The authoritative sources this skill summarizes:
`capabilities/registry.py` (the canonical-slug set, `register_canonical_slugs`, the internal
`CapabilityRegistry.register`), `capabilities/manifests.py` (`CapabilityManifest`; `_ENGINE_SPECS`, the engine's
7 generic manifests returned by `engine_capabilities()`; `MANIFEST_SPECS`, the runtime catalog; `register_capability`,
`load_pack(module)`), the reference pack's manifests (`CONTRACT_SPECS` / `COMPLIANCE_SPECS` in `rag_wright.packs.{contracts,compliance}.pack`), `capabilities/ard.py` (`EntryKind`, `MEDIA_TYPE_BY_KIND`,
`CALLABLE_KINDS`), `scripts/publish_manifests.py`, `api/invoke.py` + `capabilities/invoke.py::capability_impl` (the adapter-free impl_ref invoker + drift guard),
`api/mcp.py` (generic MCP exposure). The guardrail test is `tests/capabilities/test_authoring_contract.py`.
Paths here are relative to `src/rag_wright/` unless they start with `scripts/` or `tests/`. A product imports the
authoring surface from `rag_wright.api`: `CapabilityManifest`, `register_capability`, `load_pack`,
`engine_capabilities`, `register_canonical_slugs`, `canonical_capability_slugs`, `load_reference_pack`,
`reference_pack` (the same objects as in `capabilities/manifests.py` and `capabilities/registry.py`).

## The four surfaces (the definition of done)

A finished capability touches these: 1 (implementation) is for EVERY kind; 2 (the invoke factory + `impl_ref`) is
for invokable kinds (`subgraph`/`model`); 3 (manifest + register) is for every discoverable kind; 4 (invocable +
MCP) follows automatically for invokable kinds; 4b (a bespoke MCP server) is optional.

1. **Implementation** — the real code, in that kind's home (see per-kind below).
2. **The invoke factory + `impl_ref`** (invokable kinds: subgraph/model) — write a co-located
   `async def ainvoke(resources, inputs)` (subgraph) / `def <name>(resources, inputs)` (model) in the capability's
   own module. A subgraph factory builds over the opaque `WorkspaceHandle` (`resources._store`,
   `resources._embedder`, `resources.model_id(role)`), never env; a model factory is store-independent and ignores
   `resources`. The manifest's `impl_ref="module:attr"` points to it. The invoker imports
   it LAZILY and calls it — there is **NO central adapter dict** (EP-CORE-2). A plain function/agent_skill/mcp_tool
   declares no `impl_ref`.
3. **ARD manifest + register it** — a `CapabilityManifest(slug, kind, display_name, description,
   representative_queries=(2-5…), tags=…, impl_ref=…)`. **The catalog ships EMPTY (EP-CORE-3):** call
   `register_capability(manifest)` at runtime to add it (a product registers its own; the engine's reference pack is
   opt-in via `load_reference_pack()`). `representative_queries` is the field ARD discovery ranks on — write real,
   specific queries. For the ENGINE's reference pack, the manifest is committed in its pack's `pack.py` (`CONTRACT_SPECS` / `COMPLIANCE_SPECS`) and
   the slug in its `*_CAPABILITY_SLUGS` (added to the registry by `register_canonical_slugs` when the pack loads);
   a GENERIC engine capability's manifest is in `manifests.py::_ENGINE_SPECS` and its slug in
   `registry.ENGINE_CAPABILITY_SLUGS`. Engine capabilities (`jev_decision`, `generation`, ...) are NOT in the
   catalog until registered too: register them from `engine_capabilities()` when your pack uses them.
   **Packs:** a pack is a module exposing `register()`, which calls `register_canonical_slugs(...)` for its slugs and
   then `register_capability(m)` per manifest (plus any engine capabilities it builds on); load it with
   `load_pack("<module>")`. `load_reference_pack()` is just `load_pack("rag_wright.packs.compliance.pack")` (compliance registers contracts first).
   A downstream product can register without touching the canonical set (`register_capability` does not check
   slugs), BUT `manifests.author()` rejects a non-canonical slug, so a product that publishes ARD JSON must call
   `register_canonical_slugs` for its slugs first.
   Publish to `~/.air/registry` with `uv run python scripts/publish_manifests.py`; that script publishes only the
   engine's reference pack (it calls `load_reference_pack()`), so a product publishes its own with `publish_all`.
   Callable kinds get `ResponseBounds` (defaulted); `agent_skill` must NOT declare bounds (loaded, not called).
4. **Invocable + MCP for free** — once registered with an `impl_ref`, the capability is callable as
   `ainvoke_subgraph(slug, inputs, resources=ws)` / `invoke_model(slug, inputs, resources=ws)` (or
   `ainvoke_model(...)`, all on `rag_wright.api`). The invoker first checks the slug is in the catalog with the
   kind that invoker serves (`KeyError` / `ValueError` otherwise), then imports the `impl_ref` via
   `capabilities.invoke.capability_impl` — AND exposable over MCP (surface 4b), with **zero engine edits**.

4b. **MCP exposure** (optional) — any invokable capability is already an MCP tool with zero extra code via
   `api/mcp.py::build_capability_mcp(slug, resources=ws)` (EP-RT-2). Write a bespoke FastMCP server only
   when you want a CURATED, typed tool signature instead of the generic opaque-`inputs` surface.

## Per-kind specifics

### subgraph — a compiled LangGraph `StateGraph`
- **Home:** a generic engine subgraph in `subgraphs/<slug>.py`; a domain subgraph in its pack, `packs/<pack>/subgraphs/<slug>.py`
  (the reference pack: `packs/contracts/subgraphs/typed_property_retrieval.py`). A `production_<slug>(*, store, ...) -> CompiledGraph` builder: `g = StateGraph(_State)`,
  add nodes/edges with `START`/`END`, `return g.compile()`. Nodes call functions/models (compose).
- **Invoke:** the co-located `async def ainvoke(resources, inputs)` factory (impl_ref target) builds + awaits the graph.
- Retry/dead-letter come from the graph scaffold, not the invoker. `CapabilityManifest` has no contract field (only
  the internal `CapabilityRegistry.register(contract=...)` takes one); where the typed I/O must be declared, set
  `capability_interface` on the manifest.

### function — a plain, typed callable
- **Home:** `capabilities/<slug>.py` (generic) or `packs/<pack>/capabilities/<slug>.py` (domain). A deterministic or model-backed callable with a Pydantic in/out contract.
- Invoker adapters for `function` are not wired yet (EP-API-2c); until then functions are composed inside
  subgraphs, not invoked standalone through the API. Still register + manifest it.

### model — a trained checkpoint behind a seam
- **Home:** a GENERIC engine model capability lives in `capabilities/` (e.g. `capabilities/jev_decision.py`); a
  domain model (e.g. the reference pack's SetFit clause classifier and 29-dim property fleet) lives with its pack,
  in the reference pack (`rag_wright.packs.contracts.spans.model_capabilities`) for the engine's worked example, or in
  your product repo. Load the checkpoint ONCE and cache it (the fleet is heavy).
- Serve behind the existing seam/adapter so nothing upstream changes (to FIND where a model cap belongs, use the
  `classifier-opportunity-analysis` skill; to BUILD/train + checkpoint + serve it, the `setfit` skill). The impl_ref
  factory is `def <slug>(resources, inputs)` for a SYNC impl (CPU-bound local inference — a classifier/XGBoost
  checkpoint; `resources` ignored) or `async def <slug>(resources, inputs)` for an ASYNC impl (I/O-bound — an
  LLM-backed model cap calling OpenRouter / a local vLLM client). `invoke_model` runs a sync impl and REFUSES an
  async one; `ainvoke_model` (EP-API-7) off-loads a sync impl with `asyncio.to_thread` and awaits an async impl
  directly, with an optional `sem` for fan-out backpressure.

### agent_skill — authored SKILL.md + the Deep Agents runtime
- **Home:** `skills/<slug>/SKILL.md` (generic) or `packs/<pack>/skills/<slug>/SKILL.md` (domain, e.g.
  `packs/compliance/skills/compliance_judgment/SKILL.md`) + the agent runtime (e.g. `skills/rlm/`). It is LOADED (progressive
  disclosure), not called: no `ResponseBounds`. Declare `requires=(...)` for a closure over other skills and
  `skill_runtime` for its intrinsic runtime.

### mcp_tool — a capability exposed over MCP
- A distinct ARD identity (`<slug>_mcp`) for the same underlying capability exposed as a cross-agent MCP tool.
  Prefer the generic `build_capability_mcp` (surface 4b); author a bespoke server only for a curated typed
  signature (the reference pack's are in `packs/contracts/mcp/` and `packs/compliance/mcp/`, e.g.
  `packs/compliance/mcp/compliance_server.py`). Bind the store server-side (issue 0035) — the tool never takes a tenant/store argument.

## Verify (the guardrail)

Run `uv run pytest tests/capabilities/test_authoring_contract.py tests/capabilities/test_manifests.py
tests/capabilities/test_registry.py tests/arch/test_import_contracts.py` after authoring. It pins the contract this
skill teaches: no manifest under a non-canonical slug; the reserved-without-manifest set is a fixed allowlist (so
adding a slug but forgetting its manifest FAILS here); every manifest kind is a real ARD kind; every cap that declares
an `impl_ref` is a canonical slug with a manifest of the matching kind. The guardrail does not import the
`impl_ref` itself: add a test of your own that resolves it with `capability_impl(slug)` and calls it (the reference
pack's is `tests/spans/test_model_capabilities.py`). If you deliberately add a reserved/internal slug (no manifest),
add it to `_RESERVED_WITHOUT_MANIFEST` with a one-line reason. The engine's `tests/conftest.py` loads the reference
pack, so these tests see its slugs.

**The import boundary (engine repo).** `pyproject.toml` `[tool.importlinter]` has two forbidden contracts: every
generic engine package (`source_modules`: `rag_wright.api`, `capabilities`, `contracts`, `corpus`, `ingestion`,
`models`, `okf`, `ontology`, `skills`, `spans`, `store`, `subgraphs`, `util`) must never import `rag_wright.packs`;
and `rag_wright.packs.contracts` must never import `rag_wright.packs.compliance`. A new generic top-level package
goes in the first contract's `source_modules`; a new module inside an existing package or inside `rag_wright.packs`
needs no edit. `tests/arch/test_import_contracts.py` enforces both.

**The network guard.** `tests/conftest.py` fails any test that resolves a non-local host unless it carries a live
marker (`model`, `store`, `parse`, `embed`, `rerank`, `ner`, `fleet`). Mock model and HTTP calls in unit tests, or
mark the test live.

## Common mistakes

- Adding the slug but forgetting the manifest (slug becomes silently un-discoverable) — the guardrail catches it.
- An impl_ref factory that reaches env/globals instead of the `WorkspaceHandle` — breaks multi-workspace use; build
  everything from `resources`.
- A heavy import at the top of an impl_ref factory module that something light imports (a pack's `register()`
  module, `rag_wright.api`): it inflates the light index; import inside the factory body.
- Declaring `response_bounds` on an `agent_skill` (constructing that `CapabilityManifest` raises `ValueError`: the
  bounds apply only to callable kinds, and a skill is loaded, not called).
- Inventing a kind. If a capability fits none of the five, flag it — do not force-fit.
