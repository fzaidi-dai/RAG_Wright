# Changelog

All notable changes to RAG_Wright are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2026-10-06

Initial release — a domain-retargetable, open-core engine for hybrid retrieval + knowledge-graph RAG. Parses
documents, chunks and embeds them, runs hybrid (dense + sparse) search with reranking, extracts a knowledge graph
with resolved entities, and answers cited, abstention-willing questions — with domain knowledge in an ontology
pack, not in code.

### Added

- **Public API — `rag_wright.api`** (the whole surface a product uses):
  - typed config (`EngineConfig`, `StoreConfig`, `EngineOptions`, `IngestOptions`) and
    `open_workspace(config, corpus=…) -> WorkspaceHandle`;
  - capability invokers: `ainvoke_subgraph`, `invoke_model`, `ainvoke_model`;
  - knowledge-graph access: `kg_read`, `kg_write`, `kg_edges`, `entities_by_name`, `span_positions`;
  - documents: `parse_document`, `aparse_document`, `source_document`; id/format helpers `document_of`, `id_source`,
    `decode_bbox`;
  - usage accounting: `measure_usage` (+ `UsageTotals`, `ModelUsage`);
  - capability registration: `register_capability`, `load_reference_pack`, `reference_pack`;
  - discovery: `capability_index` (flat listing) and **`discover(task, resources=ws)`** — embedding-ranked
    capability selection for an agent planning over the engine.
- **Capability runtime (ARD).** Capabilities are registered under a manifest with an adapter-free `impl_ref` and
  invoked by name with zero engine edits; the catalog ships **empty** and is developer-registered (ADR-0117/0118).
- **One store, two seams.** A single ArcadeDB database holds both the hybrid retrieval index and the knowledge
  graph; a model-profile seam selects models by role (OpenRouter by default, self-hosted vLLM supported).
- **Ontology-driven knowledge (ADR-0066).** Closed vocabularies, KG schema, SHACL constraints and mappings live in
  a `.ttl` pack — a new domain is a pack, not a fork.
- **Reference pack.** A runnable contract/compliance worked example (opt-in via `load_reference_pack()`, 36
  capabilities) that doubles as a template for authoring your own domain.
- **Documentation.** Concepts, architecture, installation, configuration, a runnable quickstart
  (`examples/quickstart.py`), the reference-pack guide, a domain-adaptation guide (ontology authoring, KG
  construction, entity resolution, authoring capabilities, classification & decision models), and a generated API
  reference.
- **Packaging.** `uv build`-ready and PyPI-publishable; ships `py.typed`; optional spaCy NER via the `[ner]` extra
  (the model is a runtime download, not a packaged dependency); MIT licensed.

### Notes

- **Alpha**, Python 3.12. The engine is domain-neutral and depends one-way from any product (Product → Engine). The
  bundled contract/compliance pack is a **reference example, not the product**; restrictively-licensed evaluation
  corpora (CUAD/ACORD) are not shipped.

[0.1.0]: https://github.com/fzaidi-dai/RAG_Wright/releases/tag/0.1.0
