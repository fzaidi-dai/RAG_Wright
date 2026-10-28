# Changelog

All notable changes to RAG_Wright are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.15.0](https://github.com/fzaidi-dai/RAG_Wright/compare/0.14.1...0.15.0) (2026-10-28)


### Features

* **PS-51:** building-a-product-frontend draws the shell and ships its scaffold ([a16df0e](https://github.com/fzaidi-dai/RAG_Wright/commit/a16df0e8903cd43bf639ec3e2be11ae2bb242b08))


### Bug Fixes

* **PS-52:** a workspace builds one embedder however many threads use it first ([8e1617c](https://github.com/fzaidi-dai/RAG_Wright/commit/8e1617cee110fd909634b818ae2b6850d26a5a53))
* **PS-53:** KG calls refuse a type or field name that is not an identifier ([12e55fa](https://github.com/fzaidi-dai/RAG_Wright/commit/12e55fa58739d22a3e01ee6187c7efd2a932cb27))

## [0.14.1](https://github.com/fzaidi-dai/RAG_Wright/compare/0.14.0...0.14.1) (2026-10-28)


### Bug Fixes

* **PS-49:** a retried ArcadeDB write conflict no longer prints as an error ([32f649f](https://github.com/fzaidi-dai/RAG_Wright/commit/32f649f94ac1b864ee5b22460d3ef401dabbbf6a))
* **PS-50:** ensure_pack_schema declares a property a pack gains on an existing type ([c27cf40](https://github.com/fzaidi-dai/RAG_Wright/commit/c27cf40dba2512f809ba1ff06affc58d7f25f45f))

## [0.14.0](https://github.com/fzaidi-dai/RAG_Wright/compare/0.13.0...0.14.0) (2026-10-17)


### Features

* **LGI-1:** each document is ingested through a LangGraph graph of the stages ([24c15df](https://github.com/fzaidi-dai/RAG_Wright/commit/24c15dfc692b463382f18b183c551127e7092c0c))
* **LGI-2:** the whole ingest run is one LangGraph graph ([e38e9c8](https://github.com/fzaidi-dai/RAG_Wright/commit/e38e9c8733fb0bcb4da51e71e3e7093fbde4b9c3))
* **LGI-3:** an interrupted ingest resumes from its last finished stage ([9970189](https://github.com/fzaidi-dai/RAG_Wright/commit/9970189d6d5759dc4a1eeaccb35d734cc05e886e))

## [0.13.0](https://github.com/fzaidi-dai/RAG_Wright/compare/0.12.0...0.13.0) (2026-10-15)


### Features

* **PS-35:** migrate a pre-0.2.0 database's Span fields through rag_wright.api ([856f956](https://github.com/fzaidi-dai/RAG_Wright/commit/856f956f674c731d170878d44ec3fef980a10ba9))
* **PS-44:** range filters on any number of KG fields, evaluated in the store (TexWright ER-1) ([cc62c49](https://github.com/fzaidi-dai/RAG_Wright/commit/cc62c49000161567869d46127bb158db16106c28))
* **PS-45:** a public hybrid span search on rag_wright.api (engine gap G24) ([4aa802f](https://github.com/fzaidi-dai/RAG_Wright/commit/4aa802fdc6cf12a3c732aa7a41e4de6240dd05da))
* **PS-47:** find cached parses holding text a VLM invented for a blank page ([ff92d81](https://github.com/fzaidi-dai/RAG_Wright/commit/ff92d81956590002418ef50f41dee54e0b0b6ef8))


### Bug Fixes

* **PS-46:** a page with no ink is blank, never escalated to the VLM (TexWright 2026-10-15) ([fc633a8](https://github.com/fzaidi-dai/RAG_Wright/commit/fc633a84aa52a89de5b79d90b50d80f7d622d792))

## [0.12.0](https://github.com/fzaidi-dai/RAG_Wright/compare/0.11.0...0.12.0) (2026-10-15)


### Features

* **PS-43:** worker_spec(definition=), the harness design reference, and the harness source cleared of its originating product's vocabulary ([c19ff4d](https://github.com/fzaidi-dai/RAG_Wright/commit/c19ff4da006611383c6a7bca07ba633e345b44d8))

## [0.11.0](https://github.com/fzaidi-dai/RAG_Wright/compare/0.10.0...0.11.0) (2026-10-15)


### Features

* **PS-40:** ship the product-building skills (specifying, backend, front end) and correct the product-starter templates for 0.10.0 ([68ec002](https://github.com/fzaidi-dai/RAG_Wright/commit/68ec002ba566da31ea09dc24e7e959fcb6515eb0))

## [0.10.0](https://github.com/fzaidi-dai/RAG_Wright/compare/0.9.0...0.10.0) (2026-10-13)


### Features

* **PS-32:** adopt the agent harness substrate as rag_wright.harness, the third public tier (ADR-0139) ([c265c8a](https://github.com/fzaidi-dai/RAG_Wright/commit/c265c8ad352ac939496e0c20dee82bb2d6da341a))
* **PS-39:** an embedder on rag_wright.api (WorkspaceHandle.embedder, query_embedder) for a product seam that may not import pack_sdk ([4158640](https://github.com/fzaidi-dai/RAG_Wright/commit/4158640f456cb512a2dcf6b99db83aeb6f6e2c0f))

## [0.9.0](https://github.com/fzaidi-dai/RAG_Wright/compare/0.8.0...0.9.0) (2026-10-13)


### Features

* **PS-36:** agent_chat_model, an agent's chat model from a model profile, metered and traced ([f1af92a](https://github.com/fzaidi-dai/RAG_Wright/commit/f1af92a848c2de7dd02cf1d99ce61cd3df8d20d9))
* **PS-37:** drop_workspace and temporary_workspace, so a product removes the databases it creates ([44c0c78](https://github.com/fzaidi-dai/RAG_Wright/commit/44c0c78b0b8dd02724920391a564f3cda006c41c))
* **PS-38:** close the public-surface gaps a new product hits (G12, G14, G22, G23) and require ARCADEDB_PORT ([0d0c1f1](https://github.com/fzaidi-dai/RAG_Wright/commit/0d0c1f16b95bc763dec348a59965de2e8b4a22c9))

## [0.8.0](https://github.com/fzaidi-dai/RAG_Wright/compare/0.7.0...0.8.0) (2026-10-12)


### Features

* **EV-1:** aevaluate_ingestion for a web backend, with progress and a JSON-ready result ([7e6377a](https://github.com/fzaidi-dai/RAG_Wright/commit/7e6377a8994764c11f08dcf8cdb47633e6f4660c))
* **EV-2:** gold-set and report contracts, and gold import from CSV, XLSX and JSONL ([4e72d36](https://github.com/fzaidi-dai/RAG_Wright/commit/4e72d368a24045df2a09f0fc3a221c15985cbf73))
* **EV-3a:** classification and candidate-then-choose scorers, with parity on saved engine outputs ([977f9a8](https://github.com/fzaidi-dai/RAG_Wright/commit/977f9a8aa59b932d4cc1325bb93ffc6edf4b36a1))
* **EV-3b:** retrieval and relational scorers, with parity on the ACORD bar and the EDGAR graph leg ([b27b86f](https://github.com/fzaidi-dai/RAG_Wright/commit/b27b86f90d79c68f173f1842b99201a73e634870))
* **EV-3c:** extraction and judgment scorers, with parity on party extraction, ContractNLI and the semantic judge ([f1fc319](https://github.com/fzaidi-dai/RAG_Wright/commit/f1fc31973dd1d65d20d07578d7d41d6b6e91565f))
* **EV-3d:** generation and ingestion scorers; all eight scorer kinds done ([b082938](https://github.com/fzaidi-dai/RAG_Wright/commit/b082938db3bc42d81584840d63bc79f0a0ec1d65))
* **EV-4:** the eval runner (arun_eval, run_eval, rescore_eval) ([a1c0512](https://github.com/fzaidi-dai/RAG_Wright/commit/a1c0512b24fdef245869c854494b42ab8040e5b3))
* **EV-5:** the optional Langfuse backend; model calls nest under their eval item ([fbbc729](https://github.com/fzaidi-dai/RAG_Wright/commit/fbbc72911733392837bae52ac105ab80d2a922a2))
* **PS-27:** a missing database is created visibly, or refused with create=False ([2e404f2](https://github.com/fzaidi-dai/RAG_Wright/commit/2e404f2bd44bacf278c2314e6ccf412e72b336c9))
* **PS-31:** rag_wright.api.pdf_page_count, a locked PDF page count for products ([bf83f57](https://github.com/fzaidi-dai/RAG_Wright/commit/bf83f57905f8c9480c2f7890633b7ba3e474cd09))


### Bug Fixes

* **PS-24:** async model calls work on every event loop in a process (ADR-0135) ([ebd72b2](https://github.com/fzaidi-dai/RAG_Wright/commit/ebd72b2b8b962e20dadb48158d2fb429430038e3))
* **PS-26:** spreadsheet tables are glued back across blank rows, so no record is read as a header (ADR-0136) ([d0f31ac](https://github.com/fzaidi-dai/RAG_Wright/commit/d0f31ac7628a9aabd079630f18fc86a8da617cfb))
* **PS-28:** a generated answer states the evidence's support, and "none" is an abstention (ADR-0137) ([cb98642](https://github.com/fzaidi-dai/RAG_Wright/commit/cb9864236d076aea7bf31e09cab18a8eb4575dc2))
* **PS-29:** PDF parses run one at a time and the engine reaches pdfium through one locked door (ADR-0138) ([f7a17ad](https://github.com/fzaidi-dai/RAG_Wright/commit/f7a17ad7645d0bd269f955e51059a431ce5bad17))

## [0.7.0](https://github.com/fzaidi-dai/RAG_Wright/compare/0.6.0...0.7.0) (2026-10-11)


### Features

* **PS-20:** the model server cold-starts from a Modal GPU memory snapshot behind a public-route gate (ADR-0132) ([1305237](https://github.com/fzaidi-dai/RAG_Wright/commit/13052378c57f31d126d281cda49c614ca9e7207b))
* **PS-22:** the model-call deadline follows the configured timeout; a profile caps thinking models' structured output (ADR-0133) ([d327813](https://github.com/fzaidi-dai/RAG_Wright/commit/d3278130aaae10e7af3c326e503612337e7336cb))

## [0.6.0](https://github.com/fzaidi-dai/RAG_Wright/compare/0.5.1...0.6.0) (2026-10-10)


### ⚠ BREAKING CHANGES

* **PS-21:** a self-hosted server call or deploy with VLLM_API_KEY (or the profile's api_key_env) unset now raises instead of using the default key "rw-vllm-dev-key".

### Bug Fixes

* **PS-21:** no default model-server key; concurrency ceiling, status and clearer errors (ADR-0131) ([a73a4fd](https://github.com/fzaidi-dai/RAG_Wright/commit/a73a4fd9595a9f8d70d95d393147ba10a1f1718b))

## [0.5.1](https://github.com/fzaidi-dai/RAG_Wright/compare/0.5.0...0.5.1) (2026-10-10)


### Bug Fixes

* **PS-19:** a runnable model-server example for products; async Modal URL lookup ([35cd80f](https://github.com/fzaidi-dai/RAG_Wright/commit/35cd80f003453dfbb648df020a03ffe85ef64826))

## [0.5.0](https://github.com/fzaidi-dai/RAG_Wright/compare/0.4.0...0.5.0) (2026-10-10)


### Features

* **PS-19:** deploy the self-hosted model server from the engine and use it like any model ([5cd4d22](https://github.com/fzaidi-dai/RAG_Wright/commit/5cd4d22b6e684052b2d75162df32078fdf1cf2e6))


### Bug Fixes

* **PS-18:** scikit-learn floor 1.9.1 for the shipped heads; Laya temperatures recorded (ADR-0129) ([2350de4](https://github.com/fzaidi-dai/RAG_Wright/commit/2350de44a877bd74ad4e920c39a1557d43a0f2c8))

## [0.4.0](https://github.com/fzaidi-dai/RAG_Wright/compare/0.3.1...0.4.0) (2026-10-09)


### ⚠ BREAKING CHANGES

* **PS-17:** rag_wright.pack_sdk.DEFAULT_GENERAL is removed (name the model you want instead), and the legacy tag-parse extractor no longer runs a Gemma list pass by default (set RAG_INGEST_LIST_MODEL to keep one).

### Features

* **PS-13:** document metadata on IngestSource ([cf2372d](https://github.com/fzaidi-dai/RAG_Wright/commit/cf2372d6ed9ecde761b823d322f7944a221f59d5))
* **PS-14:** every role-based model call honours the workspace's EngineConfig.models ([a2d086a](https://github.com/fzaidi-dai/RAG_Wright/commit/a2d086a30026146fd78e068c647c12a3a2bd3c69))
* **PS-16:** a workspace's configuration change takes effect without a restart ([d5f0bcd](https://github.com/fzaidi-dai/RAG_Wright/commit/d5f0bcd5f3dc6a88634caf4a64d6728078a22752))
* **PS-R4:** unit-labelling eval in evaluate_ingestion ([2d33a31](https://github.com/fzaidi-dai/RAG_Wright/commit/2d33a3178a220b18c5c745d6f30168e1240a2abc))


### Code Refactoring

* **PS-17:** no Gemma default anywhere ([427fac0](https://github.com/fzaidi-dai/RAG_Wright/commit/427fac0f88b1772c2bb3fbbe47f1ec09f4f16d15))

## [0.3.1](https://github.com/fzaidi-dai/RAG_Wright/compare/0.3.0...0.3.1) (2026-10-09)


### Bug Fixes

* **PS-10:** the release build ships the engine skills ([c505ba6](https://github.com/fzaidi-dai/RAG_Wright/commit/c505ba609826976bd1ff4744e56737207bf1d0ba))

## [0.3.0](https://github.com/fzaidi-dai/RAG_Wright/compare/0.2.1...0.3.0) (2026-10-09)


### ⚠ BREAKING CHANGES

* **PS-R5c:** `ModelRole.OKF_ENRICHMENT` (and the `RAG_MODEL_OKF_ENRICHMENT` env override) is removed, and the reference pack no longer offers `okf_compile` / `okf_navigate`.

### Features

* **PS-10:** ship the engine skills in the wheel ([e6dd835](https://github.com/fzaidi-dai/RAG_Wright/commit/e6dd835415d35b38e4d8f3a123a1b8ba15cbeb9f))
* **PS-1:** export ModelRole from rag_wright.api (G17) ([a27aae1](https://github.com/fzaidi-dai/RAG_Wright/commit/a27aae1fe4738331c23f9cc0418c21a16f8adab7))
* **PS-2:** parse and ingest document bytes (G16) ([809e227](https://github.com/fzaidi-dai/RAG_Wright/commit/809e2270027d707639dac9878b682788ab4ceb9f))
* **PS-3:** export metering and trace correlation (G18) ([868a18c](https://github.com/fzaidi-dai/RAG_Wright/commit/868a18c6e0458531202ffad5f4e135c9e2506ad2))
* **PS-4:** export answer generation and relevance judgment (G19) ([aa741f6](https://github.com/fzaidi-dai/RAG_Wright/commit/aa741f6149652c00b37ebb81289311ef77c90e79))
* **PS-5:** one models root for every classifier; publish and fetch reference weights (G20) ([0f549bf](https://github.com/fzaidi-dai/RAG_Wright/commit/0f549bf6ec1e3e152eae15826044818197b82ba6))
* **PS-6:** KG count, delete and update primitives and key_range on kg_read (G21) ([f36b081](https://github.com/fzaidi-dai/RAG_Wright/commit/f36b081957eeed4a1dc12976df627049cc713927))
* **PS-7:** pack_store accessor for pack store extensions (G15) ([4fe4a36](https://github.com/fzaidi-dai/RAG_Wright/commit/4fe4a36eb8dae91097725b7bba87823c6f659167))
* **PS-8b:** the rag_wright.pack_sdk tier; generic helpers made public; StoreConfig.from_env() ([6405943](https://github.com/fzaidi-dai/RAG_Wright/commit/64059432dde24f0f603bc1b28379b5414ebce5d9))
* **PS-R3:** unit_representative hook; the reference pack labels provisions by an operative-span vote ([bbd9250](https://github.com/fzaidi-dai/RAG_Wright/commit/bbd925090cef7d950b8c5a60218ce3d6f4056bf7))
* **PS-R5b:** domain-neutral chunking prompts and entity rules; build_ingestion(chunk_discoverer=) ([66b7a6d](https://github.com/fzaidi-dai/RAG_Wright/commit/66b7a6d3bedc2f845c01355644013431f30ddab7))


### Bug Fixes

* **PS-R1:** unpin Qwen3.8-27b OpenRouter routing (ADR-0125) ([3c45c1f](https://github.com/fzaidi-dai/RAG_Wright/commit/3c45c1f2aa48562e63a0997a228ba720e231fd79))
* **PS-R2:** a failed graph extraction keeps the document's clauses (graph partial) ([bfa2b43](https://github.com/fzaidi-dai/RAG_Wright/commit/bfa2b43c29a72ab94509cbb13736609f32adb883))
* **PS-R5a:** domain vocabulary out of the generic engine's public surface ([61ef9fe](https://github.com/fzaidi-dai/RAG_Wright/commit/61ef9fe1f0016c1535287defb908b54972d7ddf9))


### Code Refactoring

* **PS-R5c:** retire the OKF code (ADR-0127) ([2470e2e](https://github.com/fzaidi-dai/RAG_Wright/commit/2470e2e0528bee2d0f032a74f274974ae0a7dd46))

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
