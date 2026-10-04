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
`capabilities/registry.py` (the canonical-slug set + `CapabilityRegistry.register`), `capabilities/manifests.py`
(`CapabilityManifest` + `_SPECS`/`MANIFEST_SPECS`), `capabilities/ard.py` (`EntryKind`, `MEDIA_TYPE_BY_KIND`,
`CALLABLE_KINDS`), `scripts/publish_manifests.py`, `api/invoke.py` + `capabilities/invoke.py::capability_impl` (the adapter-free impl_ref invoker + drift guard),
`api/mcp.py` (generic MCP exposure). The guardrail test is `tests/capabilities/test_authoring_contract.py`.

## The four surfaces (the definition of done)

A finished capability touches these: 1 (implementation) is for EVERY kind; 2 (the invoke factory + `impl_ref`) is
for invokable kinds (`subgraph`/`model`); 3 (manifest + register) is for every discoverable kind; 4 (invocable +
MCP) follows automatically for invokable kinds; 4b (a bespoke MCP server) is optional.

1. **Implementation** — the real code, in that kind's home (see per-kind below).
2. **The invoke factory + `impl_ref`** (invokable kinds: subgraph/model) — write a co-located
   `async def ainvoke(resources, inputs)` (subgraph) / `def <name>(resources, inputs)` (model) in the capability's
   own module (model factories ignore `resources`; they build over the opaque `WorkspaceHandle` — `resources._store`,
   `resources.model_id(role)`, never env). The manifest's `impl_ref="module:attr"` points to it. The invoker imports
   it LAZILY and calls it — there is **NO central adapter dict** (EP-CORE-2). A plain function/agent_skill/mcp_tool
   declares no `impl_ref`.
3. **ARD manifest + register it** — a `CapabilityManifest(slug, kind, display_name, description,
   representative_queries=(2-5…), tags=…, impl_ref=…)`. **The catalog ships EMPTY (EP-CORE-3):** call
   `register_capability(manifest)` at runtime to add it (a product registers its own; the engine's reference pack is
   opt-in via `load_reference_pack()`). `representative_queries` is the field ARD discovery ranks on — write real,
   specific queries. For the ENGINE's reference pack, the manifest is committed in `manifests.py::_SPECS` and the
   slug is in `CANONICAL_CAPABILITY_SLUGS`; a downstream product registers freely (its slugs need not be canonical).
   Publish to `~/.air/registry` (what GraphWright's store loads) with `uv run python scripts/publish_manifests.py`.
   Callable kinds get `ResponseBounds` (defaulted); `agent_skill` must NOT declare bounds (loaded, not called).
4. **Invocable + MCP for free** — once registered with an `impl_ref`, the capability is callable as
   `ainvoke_subgraph(slug, inputs, resources=ws)` / `invoke_model(slug, inputs, resources=ws)` — the invoker resolves
   the `impl_ref` via `capabilities.invoke.capability_impl` (the drift guard asserts it resolves to a callable of the
   declared kind) — AND exposable over MCP (surface 4b), with **zero engine edits**.

4b. **MCP exposure** (optional) — any invokable capability is already an MCP tool with zero extra code via
   `api/mcp.py::build_capability_mcp(slug, resources=ws)` (EP-RT-2). Write a bespoke `mcp/<slug>_server.py` only
   when you want a CURATED, typed tool signature instead of the generic opaque-`inputs` surface.

## Per-kind specifics

### subgraph — a compiled LangGraph `StateGraph`
- **Home:** `subgraphs/<slug>.py`. A `production_<slug>(*, store, ...) -> CompiledGraph` builder: `g = StateGraph(_State)`,
  add nodes/edges with `START`/`END`, `return g.compile()`. Nodes call functions/models (compose).
- **Invoke:** the co-located `async def ainvoke(resources, inputs)` factory (impl_ref target) builds + awaits the graph.
- Retry/dead-letter come from the graph scaffold, not the invoker. The output contract is the registered `contract`.

### function — a plain, typed callable
- **Home:** `capabilities/<slug>.py`. A deterministic or model-backed callable with a Pydantic in/out contract.
- Invoker adapters for `function` are not wired yet (EP-API-2c); until then functions are composed inside
  subgraphs, not invoked standalone through the API. Still register + manifest it.

### model — a trained checkpoint behind a seam
- **Home:** `spans/` or `capabilities/` wrapping the checkpoint (e.g. the SetFit clause classifier; the 29-dim
  property fleet via `spans/property_extractor.py`). Load the checkpoint ONCE and cache it (the fleet is heavy) —
  see `spans/model_capabilities.py::_dim_registry`.
- Serve behind the existing seam/adapter so nothing upstream changes (to FIND where a model cap belongs, use the
  `classifier-opportunity-analysis` skill; to BUILD/train + checkpoint + serve it, the `setfit` skill). The impl_ref
  factory is `def <slug>(resources, inputs)` for a SYNC impl (CPU-bound local inference — a classifier/XGBoost
  checkpoint; `resources` ignored) or `async def <slug>(resources, inputs)` for an ASYNC impl (I/O-bound — an
  LLM-backed model cap calling OpenRouter / a local vLLM client). `invoke_model` runs a sync impl and REFUSES an
  async one; `ainvoke_model` (EP-API-7) off-loads a sync impl with `asyncio.to_thread` and awaits an async impl
  directly, with an optional `sem` for fan-out backpressure.

### agent_skill — authored SKILL.md + the Deep Agents runtime
- **Home:** `skills/<slug>/SKILL.md` + the agent runtime (e.g. `skills/rlm/`). It is LOADED (progressive
  disclosure), not called: no `ResponseBounds`. Declare `requires=(...)` for a closure over other skills and
  `skill_runtime` for its intrinsic runtime.

### mcp_tool — a capability exposed over MCP
- A distinct ARD identity (`<slug>_mcp`) for the same underlying capability exposed as a cross-agent MCP tool.
  Prefer the generic `build_capability_mcp` (surface 4b); author a bespoke `mcp/<slug>_server.py` only for a
  curated typed signature. Bind the store server-side (issue 0035) — the tool never takes a tenant/store argument.

## Verify (the guardrail)

Run `uv run pytest tests/capabilities/test_authoring_contract.py tests/capabilities/test_manifests.py
tests/capabilities/test_registry.py` after authoring. It pins the contract this skill teaches: no manifest under a
non-canonical slug; the reserved-without-manifest set is a fixed allowlist (so adding a slug but forgetting its
manifest FAILS here); every manifest kind is a real ARD kind; every invokable cap's impl_ref resolves to a callable whose
manifest declares the matching kind. If you deliberately add a reserved/internal slug (no manifest), add it to
`_RESERVED_WITHOUT_MANIFEST` with a one-line reason.

## Common mistakes

- Adding the slug but forgetting the manifest (slug becomes silently un-discoverable) — the guardrail catches it.
- An impl_ref factory that reaches env/globals instead of the `WorkspaceHandle` — breaks multi-workspace use; build
  everything from `h`.
- A non-lazy import at the top of an adapter — inflates the light index / `import rag_wright.api`; import inside
  the adapter body.
- Declaring `response_bounds` on an `agent_skill`, or omitting the output `contract` on registration.
- Inventing a kind. If a capability fits none of the five, flag it — do not force-fit.
