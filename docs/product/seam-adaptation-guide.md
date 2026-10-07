# Adapting a product seam to the RAG_Wright engine API

A short guide for ANY product building on the engine (not just RuleWright). After the domain-agnostic separation
(ADR-0117/0067), the engine presents a clean API + a reference domain pack, so a product's **seam** shrinks to thin
domain glue. This is how to shape it. Worked examples: `rag_wright/packs/reference_seam.py` (the contract/
compliance reference domain) and `tests/journey/incidents_domain.py` (a minimal non-contract domain).

## What a seam IS now

A seam is a thin layer that (1) opens workspaces per tenant, (2) invokes engine capabilities by name with your
inputs, (3) adds your product concerns, and (4) returns engine types your product renders. Each method is a 1–3
line composition over `rag_wright.api` + (for the reference contract/compliance domain) the reference-pack store
extensions. It holds **no** `ArcadeDBStore`, embedder, model id, or id-string parsing.

## The lift / stay rule

**Lift to the engine (delete from your seam) — anything generic or already a capability:**

| Concern | Call instead |
|---|---|
| Store connection, schema, env | `open_workspace(cfg, corpus=…)`; `EngineConfig` (+ `pack=` for your `.ttl`) — never `export_env`/`ArcadeDBStore` |
| Model choice / provider / structured-output | a model **alias** in `EngineConfig.models` — never build a model object |
| Embedding / vectors | an embedding **profile** in `cfg` — never build an embedder |
| Ingest / Q&A / retrieval / compliance legs | `ainvoke_subgraph("<cap>", inputs, resources=ws)` — never hand-build the graph |
| A per-dimension classifier / single model act | `invoke_model("<cap>", inputs, resources=ws)` |
| Parse a file to a document | `parse_document` / `aparse_document` / `source_document` |
| Generic KG reads/writes/traversal, entity lookup, span positions, id/bbox | `kg_read` / `kg_write` / `kg_edges` / `entities_by_name` / `span_positions` / `document_of` / `decode_bbox` |
| Usage / cost | `measure_usage()` (wrap a block; nesting is additive) |
| Domain reads for the *reference* contract/compliance pack | the reference-pack extensions `ContractKGStore` / `ComplianceStore` over `ws._store`, and `rag_wright/packs/compliance/invokers.py` |

**Keep in the seam (the D-bucket) — genuinely yours:**

- **Tenancy**: which corpus per tenant, and the auth/policy around opening it.
- **Scoping**: your `ScopeViolation` and scoping policy.
- **Tuned/variant capabilities**: domain-tuned variants you own (e.g. an FTC-tuned compliance check over the engine's generic one) + guardrails and the human gate.
- **Error contracts & guards**: e.g. "unknown policy" validation, product-specific exceptions.
- **Presentation**: citation/preview types, result shaping, what counts as "answered", pagination.
- **Observability routing**: the engine emits usage/progress; you route it to your telemetry (correlation ids, spans).
- **Your data sources & orchestration**: corpus adapters, use-case flows, UI.

**Your own domain (not the reference pack)** additionally builds: your `.ttl` pack (entity/edge/dimension types),
your record contracts, your domain classifiers, and your domain ingestion/query capabilities (thin subgraphs you
`register_capability(...)` with an `impl_ref` and then invoke by name). See `new-domain-developer-journey.md`.

## The composition pattern

```python
ws = seam.open(corpus="…")                                   # tenancy
report = await ainvoke_subgraph("…_ingestion", {...}, resources=ws)   # a leg, by name
hits   = await ainvoke_subgraph("…_retrieval", {...}, resources=ws)
rows   = kg_read(ws, "<YourType>", where={...})              # a generic read
with measure_usage() as totals:                              # cost of a block
    answer = await ainvoke_subgraph("…_qa", {...}, resources=ws)
```

## Adaptation checklist

1. For each seam function, ask: is it **generic** or a **capability**? If yes → delete it, call the engine.
2. Replace `export_env` + `ArcadeDBStore.from_env` + model/embedder builders with one `EngineConfig` + `open_workspace`.
3. Replace hand-built graphs with `ainvoke_subgraph`/`invoke_model` by capability name.
4. Replace id-string parsing / bbox decoding / store traversals with the API accessors.
5. Keep only the D-bucket; mark each with *why it is product-side*.
6. Run your suite green, and (if an engine import-linter is mirrored product-side) prove the seam imports only
   `rag_wright.api` + contracts + the reference facades — never `rag_wright.store.*`, an embedder, or a model id.
