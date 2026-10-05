# Building a new-domain product on the RAG_Wright engine — the build sequence

Status: **LIVING** (started 2026-10-03). The operational sequence a developer follows to build a new-domain
retrieval product (RFP/BidWright, textile/LoomMatch, …) on top of the engine package. We extend this with detail as
the engine-platform work lands. Companion to `docs/proposals/new-domain-developer-journey.md` (the target-state
journey / acceptance test) and governed by ADR-0052 (engine/product split), ADR-0066 (knowledge in the `.ttl`),
ADR-0117 (engine API + capability runtime), ADR-0118 (engine core API vs ARD; ship-empty, developer-registered).

## The sequence

1. **Install the engine as a package.** The engine is the open-core (installable; `uv add`/path dep). The product
   is a SEPARATE repo that depends on it one-way (Product → Engine, never the reverse).
2. **Configure the engine.** Construct an `EngineConfig` (store connection, model aliases by role, embedding
   profile, `options.ingest` knobs) and `open_workspace(config, corpus=…) -> WorkspaceHandle`. `corpus` = the
   backend DB name; tenancy is product-side.
3. **Author the domain `.ttl` pack(s).** The domain KNOWLEDGE lives in the ontology (closed value sets, schema,
   constraints, mappings) — ADR-0066. Co-evolves with step 4 (dependency order is `.ttl`-first, but iterative: the
   ontology drives the capabilities — classifiers classify into its closed vocab, extraction targets its schema,
   the query graphs are parameterized by it).
4. **Build the domain capabilities and register them in ARD.** The product's ingestion + query/compliance GRAPHS,
   domain functions, skills. They **compose the engine's generic primitives by direct import** (`hybrid_search`,
   `graph_query`, `fusion`, `embedding`, parsing, chunking, reranking — core API, EP-CORE-1a; NOT re-registered),
   and register only the product's OWN domain graphs/models/skills via `register_capability(manifest)` with an
   `impl_ref` (EP-CORE-2/3). A new cap is invocable (and MCP-exposable) with zero engine edits.
5. **Write each capability's EVAL — eval-first (TDD).** As soon as a capability is DEFINED (its contract +
   acceptance criterion from step 4), **before you implement it**, write its eval: a reproducible gold set + the
   metric + a pass/fail gate drawn from the acceptance criterion. This is the TDD red that the implementation turns
   green, and the regression guard thereafter; it also fixes the data design (a classifier's training data IS its
   eval). Follow the **`creating-evals`** skill (gold-set design + reliability, per-capability-KIND metrics,
   gate-vs-diagnostic, isolated harness, optional Langfuse Datasets/Experiments/Scores automation next to the
   traces we already collect). Do this per capability, interleaved with building it — not as a later phase.
6. **Ingest the corpus → populate the KG.** Run the ingestion capability over the product's corpus (PDF via
   `api.parse_document`/docling, or already-text via `api.source_document`). Most query/compliance capabilities
   READ this already-populated KG, so this must happen before they're useful.
7. **(If needed) train classifiers, then adopt them behind the seam.** Classifiers have a bootstrapping
   dependency: `.ttl` → an INITIAL LLM-based ingest/extraction (the LLM teacher) → curated labeled data → train
   (see the `setfit` skill + ADR-0030 training standard) → adopt-only-if-better → swap the classifier in behind the
   existing seam (classifier-first Step-3a, ADR-0115/0116). So classifiers come AFTER an initial ingest + data, not
   at first capability authoring. The eval from step 5 is what you A/B the LLM-teacher vs the trained classifier vs
   a System-1 decision model (Jev/Laya) on, and the bar you adopt against.
8. **Build the product seam.** The product's thin layer OVER the engine API + its registered caps: tenancy +
   scoping, product orchestration, the product-named tool surface, and the D-bucket domain/app logic (compliance
   leg wrappers, vocabulary, citation-preview types, observability→product telemetry). It wraps `open_workspace` /
   the invokers / `kg_read` / `measure_usage` — never `ArcadeDBStore`/`query_embedder`/engine id formats.
9. **(Optional) a product demo/example** for the product's own users. (NOT a "reference pack" — that term is the
   ENGINE's own worked example, the contract/compliance pack that ships in the engine repo to keep the open-core
   demoable; the product builds its domain pack in steps 3–4, not a reference pack.)

## Notes / open points (extend as we go)

- Terminology: **domain pack** = the product's `.ttl` + capabilities + classifiers (steps 3–4). **Reference pack**
  = the ENGINE's worked example (contract/compliance), opt-in via `load_reference_pack()` (EP-CORE-3), kept in the
  engine repo per ADR-0052's reference-domain-pack allowance.
- The engine ships with an **empty ARD registry** (EP-CORE-3). A fresh install registers nothing; the developer
  populates ARD with their caps; the reference pack is opt-in.
- Cross-repo seam migration (the current RuleWright product) is tracked in `docs/product/engine-api-migration-handoff.md`.
