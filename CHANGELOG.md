# Changelog

All notable changes to RAG_Wright are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.1](https://github.com/fzaidi-dai/RAG_Wright/compare/0.2.0...0.2.1) (2026-10-08)


### Bug Fixes

* **reference:** the product owns its capabilities; the reference seam registers its own ([2fe1140](https://github.com/fzaidi-dai/RAG_Wright/commit/2fe114085e84c3b71ab00f3f126c68bd81086554))

## [0.2.0](https://github.com/fzaidi-dai/RAG_Wright/compare/0.1.0...0.2.0) (2026-10-07)


### ⚠ BREAKING CHANGES

* **api:** rag_wright.api.id_source is removed; use ComplianceStore.policy_of_requirement or document_of. Recorded in docs/specs/ingestion-hooks/ing8-breaking-changes.md (API cleanup after ING-8).
* **ING-8e:** ArcadeDBStore.contract_by_id / all_contracts / clauses_in_contract / clause_positions / write_clause_exception_links / clear_property_graph / property_graph_counts / clause_property_values moved to ContractKGStore; ensure_compliance_schema / all_requirements / requirement_sources / ingested_citations moved to ComplianceStore; spans_by_contract -> spans_by_document, all_spans_by_contract -> all_spans_by_document; the CLAUSE_TYPE / PROPVALUE_TYPE / PROPERTY_EDGE_TYPE / CONTRACT_TYPE / IS_EXCEPTION_TO_EDGE_TYPE / REQUIREMENT_TYPE constants left rag_wright.store.arcadedb. Table in docs/specs/ingestion-hooks/ing8-breaking-changes.md (ING-8e).
* **ING-8d:** EngineOptions(ingest=IngestOptions(classify_concurrency=..., ...)) -> EngineOptions(packs={"contracts": ContractIngestOptions(...)}); IngestOptions.list_model / clause_samples removed. SpanRecord / stored Span fields renamed (document_id, primary_tag, tags; parent_okf_path removed); span_hybrid_search(function=) -> primary_tag=; span rows carry the new keys. Existing databases must be migrated with scripts/migrate_span_fields.py before ensure_schema / open_workspace accepts them. Full table in docs/specs/ingestion-hooks/ing8-breaking-changes.md (ING-8d).
* **ING-8c:** the reference-pack modules moved with no shims (e.g. rag_wright.spans.boundary -> rag_wright.packs.contracts.spans.boundary, rag_wright.subgraphs.contract_ingestion_pipeline -> rag_wright.packs.contracts.subgraphs.contract_ingestion_pipeline, rag_wright.reference.pack -> per-pack pack.py, rag_wright.mcp.* -> packs.<pack>.mcp.*). Manifest impl_refs changed: republish ARD JSON. The full old -> new table is in docs/specs/ingestion-hooks/ing8-breaking-changes.md (ING-8c).
* **ING-8b:** CANONICAL_CAPABILITY_SLUGS, manifests._SPECS, store.arcadedb's contract edge constants and ArcadeDBStore.clause_kg_counts / clear_clause_kg / mark_span_properties_ambiguous moved or were replaced; ExtractionResult.clause_facts removed; build_graph_extraction requires extractors. See docs/specs/ingestion-hooks/ing8-breaking-changes.md.
* **ING-8a:** EngineConfig.pack=None and ArcadeDBStore.from_env/from_config without pack_ttl no longer create the contract schema (Clause, PropertyValue, Contract, typed edges); document-scope validation needs Document nodes. Migration: pass pack_ttl=reference_pack_ttl() or call ensure_pack_schema(reference_pack_ttl()); see docs/specs/ingestion-hooks/ing8-breaking-changes.md.

### Features

* **ING-1:** ingestion hook contracts (ADR-0124) ([ab1c914](https://github.com/fzaidi-dai/RAG_Wright/commit/ab1c9146e4183deebb41bc250cd90cf489610e4a))
* **ING-2:** docling-layout default segmenter (ADR-0124) ([ddb3e92](https://github.com/fzaidi-dai/RAG_Wright/commit/ddb3e92a9552f991b8a07a7a202256c5f5e5298e))
* **ING-3:** structural unit grouper + Span.kind (ADR-0124) ([a8ca3eb](https://github.com/fzaidi-dai/RAG_Wright/commit/a8ca3eb887935c43de91b2302d54a8983a2d479d))
* **ING-4a:** spreadsheet content — hidden sheets, compact tables, record-per-row units (ADR-0124) ([4ee4b99](https://github.com/fzaidi-dai/RAG_Wright/commit/4ee4b999aba8a7686b1a6468024a8cd72e560327))
* **ING-4b:** public build_ingestion builder, tuning, packaged eval (ADR-0124) ([86a26ab](https://github.com/fzaidi-dai/RAG_Wright/commit/86a26aba2bd813fc6abad30f390fd90955930853))
* **ING-4c:** contract pipeline on the shared ingestion stages; VISION_OCR on the product LLM (ADR-0124) ([4e2e7c4](https://github.com/fzaidi-dai/RAG_Wright/commit/4e2e7c46a7ad23c81854b71ada3d964fa582df74))
* **ING-6b:** PDF attachments as child documents; empty documents ingest (ADR-0124) ([430225d](https://github.com/fzaidi-dai/RAG_Wright/commit/430225d6a02b6d33ebd549c98014098b802181d8))
* **ING-6:** embedded files as linked child documents (ADR-0124) ([892aa2e](https://github.com/fzaidi-dai/RAG_Wright/commit/892aa2ed0a761d78d11eadada9c76e607460ac38))
* **ING-7:** generic table-rows primitive (ADR-0124) ([81fc411](https://github.com/fzaidi-dai/RAG_Wright/commit/81fc411ef018d816bdf14614092d6a1c540e5ca6))
* **ING-8a:** neutral default schema; packs ensure their own (ADR-0124) ([740890e](https://github.com/fzaidi-dai/RAG_Wright/commit/740890e4f27e3e17d937e536b241f771950b836c))
* **ING-9, ING-9b:** judge and residual property values on the decision model (ADR-0040) ([88e7d9d](https://github.com/fzaidi-dai/RAG_Wright/commit/88e7d9d8e594c18d022e301cbffac271177fa8b2))


### Bug Fixes

* **ING-4d:** structural provision-boundary prompt for Jev; clause-cache position key (ADR-0122, ADR-0124) ([7012757](https://github.com/fzaidi-dai/RAG_Wright/commit/7012757a89a2869f0043846c3f0abf03af010ee8))
* **ING-5:** retry ArcadeDB write conflicts; rigorous doc audit + building-an-ingestion-capability skill ([b5da176](https://github.com/fzaidi-dai/RAG_Wright/commit/b5da176e499a35975bd27d59a37cf1c8705f9066))
* **parse:** reuse docling converter per thread instead of per document ([038abb8](https://github.com/fzaidi-dai/RAG_Wright/commit/038abb8321f1941185f73adb7e450377b4e547c3))


### Code Refactoring

* **api:** drop id_source; export the pack-authoring helpers (closes G5) ([8dea782](https://github.com/fzaidi-dai/RAG_Wright/commit/8dea782556aad7afeca4bef98fd7e45b51c22633))
* **ING-8b:** no generic module imports the reference pack (ADR-0124) ([ca5eeac](https://github.com/fzaidi-dai/RAG_Wright/commit/ca5eeacc436e457a45b5923c5128874d803bcd9d))
* **ING-8c:** move the reference pack into rag_wright.packs (ADR-0124) ([923a640](https://github.com/fzaidi-dai/RAG_Wright/commit/923a6408f376555d1dab5c8a80919b4adc1c4ab3))
* **ING-8d:** generic option and span field names (ADR-0124) ([1adfc87](https://github.com/fzaidi-dai/RAG_Wright/commit/1adfc87))
* **ING-8e:** the generic store names no domain type (ADR-0124) ([c91fba8](https://github.com/fzaidi-dai/RAG_Wright/commit/c91fba8511d37a81aa44897d8127113c0250119e))

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
