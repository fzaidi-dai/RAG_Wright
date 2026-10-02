---
name: authoring-a-capability
description: >-
  How to author a new RAG_Wright engine capability of any kind (subgraph, function, model, agent_skill, mcp_tool)
  so it is registered, ARD-discoverable, and invokable by name through the engine API. Use it whenever you add a
  new capability or a new-domain product/graph needs one: it gives the shared registration + ARD + invocation
  contract (the five surfaces + the definition of done), the per-kind implementation specifics, and the
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
`CALLABLE_KINDS`), `scripts/publish_manifests.py`, `api/invoke.py` (the invoker adapters + drift guard),
`api/mcp.py` (generic MCP exposure). The guardrail test is `tests/capabilities/test_authoring_contract.py`.

## The five surfaces (the definition of done)

A finished, discoverable, invokable capability touches up to five places. The first three are for EVERY kind; 4 is
for invokable kinds (`subgraph`/`model`); 5 is optional.

1. **Implementation** — the real code, in that kind's home (see per-kind below).
2. **Canonical slug** — add the slug to `CANONICAL_CAPABILITY_SLUGS` in `capabilities/registry.py`. Slugs are
   `snake_case`, URN-safe, and load-bearing (they are the public name). `register(...)` rejects any non-canonical
   slug.
3. **In-process registration** — a `register_<slug>(registry)` function (next to the implementation) calling
   `registry.register(slug, contract=<PydanticOutputModel>, kind=<kind>, display_name=..., description=..., tags=...)`.
   `contract` is the capability's **output** Pydantic model (the registered capability contract, per CLAUDE.md
   "contract use"). Callable kinds get `ResponseBounds` (defaulted); `agent_skill` must NOT declare bounds (it is
   loaded, not called).
4. **ARD manifest** — a `CapabilityManifest(slug, kind, display_name, description, representative_queries=(2-5…),
   tags=…)` in `manifests.py::_SPECS`. `representative_queries` is the field ARD discovery ranks on — write real,
   specific queries. Publish with `uv run python scripts/publish_manifests.py` (regenerates `~/.air/registry`;
   safe to re-run). **A slug with no manifest is RESERVED / internal-only** (not ARD-discoverable) — a deliberate
   choice pinned by the guardrail, not a default.
5. **Invoker adapter** (invokable kinds only) — wire the slug → a lazy adapter in `api/invoke.py`
   (`_SUBGRAPH_ADAPTERS` for subgraph, `_MODEL_ADAPTERS` for model). The adapter imports the implementation LAZILY
   (keeps the light index cheap), builds it over the opaque `WorkspaceHandle` (`h._store`, `h.model_id(role)`,
   never env), and invokes it. The drift guard (`_invocable_names` vs the index) forces every adapter to be a
   catalogued slug with a matching kind. Once wired, it is callable as `ainvoke_subgraph(slug, inputs, resources=ws)`
   / `invoke_model(slug, inputs, resources=ws)` AND exposable over MCP for free (surface 5b).

5b. **MCP exposure** (optional) — any invokable capability is already an MCP tool with zero extra code via
   `api/mcp.py::build_capability_mcp(slug, resources=ws)` (EP-RT-2). Write a bespoke `mcp/<slug>_server.py` only
   when you want a CURATED, typed tool signature instead of the generic opaque-`inputs` surface.

## Per-kind specifics

### subgraph — a compiled LangGraph `StateGraph`
- **Home:** `subgraphs/<slug>.py`. A `production_<slug>(*, store, ...) -> CompiledGraph` builder: `g = StateGraph(_State)`,
  add nodes/edges with `START`/`END`, `return g.compile()`. Nodes call functions/models (compose).
- **Invoke:** async — `await graph.ainvoke({...})`; the invoker adapter is `async def _sub_<slug>(h, inputs)`.
- Retry/dead-letter come from the graph scaffold, not the invoker. The output contract is the registered `contract`.

### function — a plain, typed callable
- **Home:** `capabilities/<slug>.py`. A deterministic or model-backed callable with a Pydantic in/out contract.
- Invoker adapters for `function` are not wired yet (EP-API-2c); until then functions are composed inside
  subgraphs, not invoked standalone through the API. Still register + manifest it.

### model — a trained checkpoint behind a seam
- **Home:** `spans/` or `capabilities/` wrapping the checkpoint (e.g. the SetFit clause classifier; the 29-dim
  property fleet via `spans/property_extractor.py`). Load the checkpoint ONCE and cache it (the fleet is heavy) —
  see `api/invoke.py::_dim_registry`.
- Serve behind the existing seam/adapter so nothing upstream changes (see the `setfit` skill for the training +
  checkpoint + serving discipline). Invoker adapter is `def _model_<slug>(h, inputs)` (sync; the MCP adapter
  offloads it with `asyncio.to_thread`).

### agent_skill — authored SKILL.md + the Deep Agents runtime
- **Home:** `skills/<slug>/SKILL.md` + the agent runtime (e.g. `skills/rlm/`). It is LOADED (progressive
  disclosure), not called: no `ResponseBounds`. Declare `requires=(...)` for a closure over other skills and
  `skill_runtime` for its intrinsic runtime.

### mcp_tool — a capability exposed over MCP
- A distinct ARD identity (`<slug>_mcp`) for the same underlying capability exposed as a cross-agent MCP tool.
  Prefer the generic `build_capability_mcp` (surface 5b); author a bespoke `mcp/<slug>_server.py` only for a
  curated typed signature. Bind the store server-side (issue 0035) — the tool never takes a tenant/store argument.

## Verify (the guardrail)

Run `uv run pytest tests/capabilities/test_authoring_contract.py tests/capabilities/test_manifests.py
tests/capabilities/test_registry.py` after authoring. It pins the contract this skill teaches: no manifest under a
non-canonical slug; the reserved-without-manifest set is a fixed allowlist (so adding a slug but forgetting its
manifest FAILS here); every manifest kind is a real ARD kind; every invoker adapter is a canonical slug whose
manifest declares the matching kind. If you deliberately add a reserved/internal slug (no manifest), add it to
`_RESERVED_WITHOUT_MANIFEST` with a one-line reason.

## Common mistakes

- Adding the slug but forgetting the manifest (slug becomes silently un-discoverable) — the guardrail catches it.
- An invoker adapter that reaches env/globals instead of the `WorkspaceHandle` — breaks multi-workspace use; build
  everything from `h`.
- A non-lazy import at the top of an adapter — inflates the light index / `import rag_wright.api`; import inside
  the adapter body.
- Declaring `response_bounds` on an `agent_skill`, or omitting the output `contract` on registration.
- Inventing a kind. If a capability fits none of the five, flag it — do not force-fit.
