# Building a new domain on the engine

The sequence a developer follows to build a new-domain product (contracts, textiles, policies, …) on the engine.
Each step is short here; the detail lives in the linked companion. A new domain is **config + a `.ttl` pack +
capabilities**, never an engine edit (ADR-0052 engine/product split, ADR-0066 knowledge-in-the-ontology,
ADR-0117/0118 engine API + capability runtime). Read [Concepts](../concepts.md) and
[Architecture](../architecture.md) first.

> **Dependency direction is one-way: Product → Engine, never the reverse.** Your product is a separate repo that
> depends on the engine package; it never forks or edits the engine.

## The sequence

1. **Install the engine.** `uv add rag-wright` (or a path/git dep pre-publish), plus ArcadeDB and a model provider.
   → [Installation](../installation.md).

2. **Configure the engine.** Construct an `EngineConfig` (store, model roles, embedding profile, and the options
   catalog: the generic ingest `tuning`, plus your pack's own options under `EngineOptions.packs`) and
   `open_workspace(config, corpus=…) -> WorkspaceHandle`. `corpus` is the backend DB name; tenancy is
   product-side: one corpus and one config per tenant, with the settings that stay per process listed in
   [Workspaces](../workspaces.md). To run on your own Qwen server instead of OpenRouter, deploy it from the engine
   and use its model id in `EngineConfig.models` ([A self-hosted model server](../configuration.md#a-self-hosted-model-server-qwen-on-modal)).
   → [Configuration](../configuration.md).

3. **Author the domain `.ttl` pack.** The domain KNOWLEDGE — closed value sets, the KG schema, SHACL constraints,
   mappings — lives in the ontology, declaratively, never in Python (ADR-0066). It co-evolves with step 4 (it
   drives the capabilities: classifiers classify into its vocab, extraction targets its schema, the query graphs
   are parameterized by it). Point the engine at it with `EngineConfig(pack="<your>.ttl")`: opening the workspace
   creates the vertex and edge types the pack declares. `pack=None` gives the neutral engine schema only.
   → [Ontology authoring](ontology-authoring.md).

4. **Build and register the domain capabilities.** Ingestion is no longer hand-composed: the engine's
   `build_ingestion` owns the pipeline, and your domain supplies an **extractor** plus any optional hooks
   (segmenter, span tagger, unit grouper, boundary decider, writer, a per-document hook)
   → [KG construction](kg-construction.md) and the `building-an-ingestion-capability` skill. Your query graphs, domain functions, and skills **compose the engine's
   generic primitives by direct import** (hybrid search, graph query, fusion, embedding, parsing, chunking,
   reranking) and register only your OWN domain graphs/models/skills via `register_capability(manifest)` with an
   `impl_ref`, invocable (and MCP-exposable) with zero engine edits. Package them as a pack module that exposes
   `register()` and load it with `load_pack("<your module>")`.
   → [Authoring capabilities](authoring-capabilities.md).

5. **Write each capability's EVAL first (TDD).** As soon as a capability is *defined* (contract + acceptance),
   before you implement it, write its eval: a reproducible gold set + the metric + a pass/fail gate. It's the red
   the implementation turns green, the permanent regression guard, and it fixes the data design (a classifier's
   training data IS its eval). → the **`creating-evals`** skill. Do this per capability, interleaved with building
   — not as a later phase.

6. **Ingest the corpus → populate the KG.** Build the pipeline with `build_ingestion(extractor, ...)` and run
   `await pipeline.aingest(ws, sources, cache_dir=...)` over your files (paths, or `IngestSource` for a per-source
   id, table mode or hidden-sheet choice). Per document the order is: parse, chunk, segment (and tag), index (embed
   the spans), group into units, extract, write, your `document_hook`, then a `Document` node; embedded files and
   PDF attachments follow as child documents. Before a full run, check the structure on your own samples with
   `evaluate_ingestion` and tune `IngestionTuning`. For spreadsheets and other tables, `table_rows` gives exact
   cells. Most query capabilities READ this populated KG, so it comes first.
   → [KG construction](kg-construction.md). Entities are canonicalized in your `document_hook` →
   [Entity resolution](entity-resolution.md).

7. **(If needed) train classifiers, then adopt them behind the seam.** Classifiers bootstrap from an initial
   LLM-based extraction: `.ttl` → LLM teacher → curated labels → train → adopt-only-if-better → swap in behind the
   existing seam. The step-5 eval is what you A/B the LLM teacher vs a trained classifier vs a System-1 decision
   model (Jev/Laya) on, and the bar you adopt against.
   → [Classification & decision models](classification-and-decision-models.md).

8. **Build the product seam.** Your thin layer over the engine API + registered caps: tenancy/scoping, product
   orchestration, the product-named tool surface, and domain/app logic. It wraps `open_workspace` / the invokers /
   `kg_read` / `measure_usage` — never `ArcadeDBStore` / `query_embedder` / engine id formats. (Product-repo work,
   not engine.)

9. **(Optional) a product demo** for your users. (Distinct from the engine's [reference pack](../reference-pack.md),
   which is the engine's own worked example — your domain pack is steps 3–4.)

## Terminology

- **Domain pack** = your product's `.ttl` + capabilities + classifiers (steps 3–4).
- **Reference pack** = the ENGINE's contract/compliance worked example, opt-in via `load_reference_pack()` — read it
  as a template ([reference-pack](../reference-pack.md)).
- The engine ships an **empty ARD catalog**; a fresh install registers nothing until you (or the reference pack) do.
  That includes the engine's own generic capabilities (`jev_decision`, `generation`, ...): register the ones you use
  from `engine_capabilities()` ([authoring capabilities](authoring-capabilities.md)).

## Companions

- [Ontology authoring](ontology-authoring.md) · [KG construction](kg-construction.md) ·
  [Entity resolution](entity-resolution.md) · [Authoring capabilities](authoring-capabilities.md) ·
  [Classification & decision models](classification-and-decision-models.md)
- Skills (coding-agent playbooks): `building-an-ingestion-capability`, `creating-evals`,
  `classifier-opportunity-analysis`, `setfit`, `laya`, `authoring-a-capability`.
