# Authoring capabilities

A **capability** is a named, discoverable unit of work (ARD). Your domain registers its own capabilities; the engine
invokes them by name with **zero engine edits**. The full step-by-step workflow (the four surfaces, plus an
optional bespoke MCP server; the definition of done; the conformance guardrail) is the **`authoring-a-capability`**
skill; this page is the orientation.

## The manifest

Each capability is declared by a frozen `CapabilityManifest` (`rag_wright.capabilities.manifests`):

| field | meaning |
|---|---|
| `slug` | the name it's invoked/registered under (your own — see "Registering" below) |
| `kind` | one of the five kinds below |
| `display_name`, `description` | human-facing |
| `representative_queries` | 2–5 examples; the field discovery ranks on |
| `impl_ref` | `"module:attr"` → the invoke factory (makes it invocable-by-name); `None` for non-invokable kinds |
| `golden_eval_ref` | a pointer to the capability's eval (write it first — the `creating-evals` skill) |
| `tags`, `response_bounds` | discovery tags; output bounds (callable kinds) |
| `requires`, `skill_runtime` | agent_skill only |

## The five kinds, and how each runs

| kind | what it is | how it runs |
|---|---|---|
| `subgraph` | a composite pipeline (an ingestion/retrieval/QA graph) | `ainvoke_subgraph(name, inputs, resources=ws)` |
| `model` | a model-backed decision (a classifier, a decision model, an LLM step) | `invoke_model` / `ainvoke_model(name, inputs, resources=ws)` |
| `function` | a deterministic function other capabilities compose | imported directly |
| `agent_skill` | authored `SKILL.md` knowledge loaded into an agent | loaded, not called |
| `mcp_tool` | a capability exposed over MCP | served by a deployed MCP server |

## Invocable-by-name vs composed

A capability with an `impl_ref` ("module:attr" pointing at a factory `def <slug>(resources, inputs) -> result`) is
invoked **by name** through the engine: `capability_impl` imports it lazily — there is no central engine-owned
adapter dict, so registering one makes it invocable with zero engine edits (ADR-0118). A `model` impl may be sync
(a classifier, run off the event loop by `ainvoke_model`) or async (an LLM/decision-model-backed cap, awaited).

Capabilities without an `impl_ref` are **composed by import** (most `function`s), **loaded** as agent-skill
knowledge, or **served** by a deployed MCP server. In the reference pack, 9 of 36 capabilities are
invocable-by-name; the rest are composed/loaded/served.

## Registering

```python
from rag_wright.api import register_capability
from rag_wright.capabilities.manifests import CapabilityManifest

register_capability(CapabilityManifest(
    slug="my_domain_retrieval",                 # YOUR slug — no canonical-slug restriction on this path
    kind="subgraph",
    display_name="My domain retrieval",
    description="…",
    representative_queries=("…", "…"),
    impl_ref="my_product.caps.retrieval:ainvoke",  # (resources, inputs) -> result
))
# now invocable: await ainvoke_subgraph("my_domain_retrieval", {...}, resources=ws)
```

`CapabilityManifest` is imported from `rag_wright.capabilities.manifests`; it is not re-exported from
`rag_wright.api` yet (engine gap G5).

`register_capability` places the manifest in the runtime ARD catalog (ship-empty) and imposes **no canonical-slug
restriction** — use your domain's names. (`canonical_capability_slugs()` -- the engine's generic slugs plus those each loaded pack adds with
`register_canonical_slugs` -- is the cross-spec join-key set the internal registry checks, not a constraint on your
domain.) Expose a capability over MCP by registering an `mcp_tool` surface.

**Packaging your capabilities as a pack.** Put your manifests in one module that exposes `register()`, and load it
with `load_pack("<your module>")` (`rag_wright.capabilities.manifests.load_pack`, not exported from
`rag_wright.api` yet, engine gap G5). `register()` calls `register_canonical_slugs(...)` for your slugs
(`rag_wright.capabilities.registry`), then `register_capability(m)` for each manifest. The reference pack is the
example: `load_reference_pack()` is `load_pack("rag_wright.packs.compliance.pack")` (the compliance pack registers the contracts pack it builds on first).

```python
# my_product/caps/pack.py
from rag_wright.api import register_capability
from rag_wright.capabilities.manifests import engine_capabilities
from rag_wright.capabilities.registry import register_canonical_slugs

MY_SPECS = (...)  # your CapabilityManifest objects

def register() -> None:
    register_canonical_slugs({m.slug for m in MY_SPECS})
    for m in (*engine_capabilities(), *MY_SPECS):   # include the engine capabilities you use
        register_capability(m)
```

**Engine capabilities are opt-in too.** The engine's generic capabilities (`jev_decision`, `generation`,
`vision_to_text`, `span_relevance_judgment`, and the RLM skills) are not in the empty catalog. Register them with
`for m in engine_capabilities(): register_capability(m)` (or from your pack's `register()`, as above);
otherwise `ainvoke_model("jev_decision", ...)` raises `KeyError` (unknown capability).

**Discovery.** Once registered, a product agent finds your capability two ways: `capability_index()` — the flat
`{slug: {kind, description}}` listing (what exists) — or **`discover(task, resources=ws)`** — embedding-ranked
selection over the live catalog for a task (returns the top matches by BGE-M3 similarity to each capability's
`representative_queries` + description), for an agent that must *plan* over the engine rather than invoke a known
slug. Write good `representative_queries` (2–5, phrased like real tasks) — that is the field discovery ranks on.

## Compose the engine's primitives — don't re-register them

Your capabilities **compose the engine's generic primitives by direct import** (hybrid search, graph query, fusion,
embedding, parsing, chunking, reranking); you register only your OWN domain graphs/models/skills. Don't wrap or
re-register an engine primitive.

## Definition of done

- An `impl_ref` factory with the `(resources, inputs) -> result` shape for an invocable kind.
- An **eval written first** (`golden_eval_ref`; the `creating-evals` skill) — the capability's executable acceptance.
- Passes the conformance guardrail (`tests/capabilities/test_authoring_contract.py`), which checks the manifest +
  invocation contract the `authoring-a-capability` skill specifies.

Next: [classification & decision models](classification-and-decision-models.md) — when a capability's decision is a
classifier or a System-1 decision model rather than an LLM.
