# ING-8 breaking changes (for the product migration)

ING-8 de-legalizes the engine. There are **no transition defaults and no dual reads**: each change below is final
when it lands, and a product built on the engine (RuleWright) migrates afterwards, from this record. Each entry
says what changed, who is affected, and the exact change a consumer makes.

## ING-8a: the default schema is neutral

**What changed.**
- `EngineConfig.pack = None` and `ArcadeDBStore.from_env(...)` / `from_config(...)` with no `pack_ttl` now create
  **only the engine types**: `Chunk`, `Entity`, `Relationship`, `Mentions`, `Span`, `Document`, `EmbeddedIn`,
  `AttachedTo` (+ their indexes). Before, every store also got the contract reference pack's `Clause`,
  `PropertyValue`, `Contract`, `HasProperty`, `IsExceptionTo` and its 27 typed edges (`CAPS`, `BOUNDED_BY`,
  `GOVERNED_BY`, `HAS_*`, ...); and the typed edges were created even when a different pack was configured.
- New: `ArcadeDBStore.ensure_pack_schema(ttl)` creates a pack's schema (vertex types, unique indexes, structural
  edges, typed edges) from that pack's own `.ttl`; `ArcadeDBStore.schema_packs()` lists the packs a store has.
  `rag_wright.ontology.loader.reference_pack_ttl()` is the reference contract pack's `.ttl`.
- The reference contract pack ensures its own schema on use: `ContractKGStore(store)` (used by the reference
  contract pipeline, intra-document QA, property-boosted retrieval and the reference seam) calls
  `ensure_pack_schema(reference_pack_ttl())` once per store.
- `kg_write` encodes node properties by the schemas of the store's packs only (`schema_packs()`); a neutral store
  no longer encodes by the contract types.
- `ArcadeDBStore.known_document_ids()` (document-scope validation for `graph_query` / retrieval scopes) now reads
  the generic `Document` nodes (`doc_id`), not the `Contract` nodes. Both pipelines write a `Document` per ingested
  source document; a database ingested before ING-4b has no `Document` nodes and must be re-ingested (or have
  `Document` nodes written) for its documents to validate.

**Who is affected.** Any consumer that opens a store without a pack and then reads or writes contract types
directly (not through `ContractKGStore`), e.g. `ArcadeDBStore.from_env(database=...)` + `.ensure_schema()` and
then `clauses_in_contract`, `all_contracts`, `clause_kg_counts`, `span_properties` (RuleWright:
`rulewright/engine/seam.py` and its other `from_env` sites).

**Migration.** Either pass the reference pack when opening the store --
`ArcadeDBStore.from_env(database=..., pack_ttl=reference_pack_ttl())` /
`EngineConfig(..., pack=reference_pack_ttl())` -- or call `store.ensure_pack_schema(reference_pack_ttl())` once
before using contract types. For document-scope validation, make sure every ingested document has a `Document` node.

## ING-8b: no generic module imports the reference pack

The import contract "Generic engine foundation is domain-free" now covers EVERY generic module and forbids the WHOLE
reference pack (pyproject `[tool.importlinter]`; enforced by `tests/arch/test_import_contracts.py`). Moves and
removals a consumer must follow:

| Before | After |
|---|---|
| `store.arcadedb.TYPED_PROPERTY_EDGE_TYPES`, `_DIM_EDGE_STR`, `_EDGE_PREDICATE_IRI`, `_edge_predicate_iri`, `_stale_property_statements` | `capabilities.contract_kg_store` (same names) |
| `ArcadeDBStore.clause_kg_counts()`, `.clear_clause_kg()`, `.mark_span_properties_ambiguous(span_ids)` | `ContractKGStore(store).clause_kg_counts()` / `.clear_clause_kg()` / `.mark_span_properties_ambiguous(span_ids)` |
| `ArcadeDBStore.ensure_pack_schema(ttl)` also created the contract typed property edges | it creates only what a pack declares in the engine vocabulary (vertex types, unique indexes, structural edges); the contract typed edges are created by `ContractKGStore` (new generic `ArcadeDBStore.ensure_edge_types(names)`) |
| `ontology.loader.load_kg_schema` / `KgVertexType` | the generic reader is `ontology.pack_schema.load_kg_schema(path)` (path required) / `KgVertexType`; `ontology.loader` re-exports both (its `load_kg_schema(None)` still means the reference pack) |
| `capabilities.registry.CANONICAL_CAPABILITY_SLUGS` (a frozenset of 41, mostly contract/compliance) | `capabilities.registry.canonical_capability_slugs()` -- the 11 `ENGINE_CAPABILITY_SLUGS` plus those of each loaded pack; a pack adds its own with `register_canonical_slugs(...)`. Registering a reference-pack slug requires `load_reference_pack()` first |
| `capabilities.manifests._SPECS` (all 36 manifests) | `manifests._ENGINE_SPECS` (7 generic: generation, the RLM skills, vision_to_text, span_relevance_judgment, jev_decision) + `reference.pack.REFERENCE_SPECS` (29); `load_reference_pack()` registers both (unchanged result); new `load_pack("<module>")` loads any pack module exposing `register()`; `engine_capabilities()` lists the generic manifests |
| `contracts.ontology.EntityNode`, `RelationshipFact` | generic home `contracts.graph` (`contracts.ontology` re-exports them) |
| `ExtractionResult.clause_facts` | removed (it was never populated) |
| `subgraphs.typed_clause_extraction.TransientExtraction` | generic home `subgraphs.scaffold.TransientExtraction` (re-exported at the old path) |
| `subgraphs.graph_extraction.build_graph_extraction(extractors=None)` defaulted to the contract party extractor | `extractors` is REQUIRED; the reference stack is `capabilities.graph_extraction.default_extractors()` |
| `capabilities.remote_encoders.query_classifier`, `RemoteLegalBertClassifier` | `spans.legalbert_classifier` (same names) |
| `capabilities.highlight_serve._decode_bbox` | generic `contracts.span.decode_bbox` (`api.ids.decode_bbox` unchanged) |

Reclassified as reference pack (no API change): `subgraphs.async_ingestion`, `corpus.gcs_ingestion`.

## ING-8c: the reference pack moved to `rag_wright.packs` (no shims)

The reference pack is now two domain packs: **`rag_wright.packs.contracts`** and **`rag_wright.packs.compliance`**
(built on contracts). Module paths mirror the old subpackages, except that the pack's Pydantic contracts live under
`schemas` (`rag_wright.contracts.property` -> `rag_wright.packs.contracts.schemas.property`). **The old paths no
longer exist** (no re-export shims): every import, `monkeypatch` target, `impl_ref` and `python -m` path that names
an old module must change. The generic engine (`rag_wright.api`, `capabilities`, `contracts`, `corpus`,
`ingestion`, `models`, `ontology`, `spans`, `store`, `subgraphs`, ...) did not move.

**Registration.**
- `rag_wright.reference.pack` is gone. `REFERENCE_CAPABILITY_SLUGS` / `REFERENCE_SPECS` (30 slugs, 29 manifests)
  are split into `packs.contracts.pack.CONTRACT_CAPABILITY_SLUGS` / `CONTRACT_SPECS` (21 / 20) and
  `packs.compliance.pack.COMPLIANCE_CAPABILITY_SLUGS` / `COMPLIANCE_SPECS` (9 / 9). Compliance's `register()`
  registers the contracts pack first.
- `load_reference_pack()` (unchanged name and result) is now `load_pack("rag_wright.packs.compliance.pack")`;
  `reference_pack()` returns the 7 engine manifests + `CONTRACT_SPECS` + `COMPLIANCE_SPECS` (36, unchanged).
- Every manifest's `impl_ref` now names the new module; republish any ARD JSON generated before ING-8c.

**Ontology loader split.** The compliance readers moved out of the contracts loader into
`packs.compliance.ontology.loader`: `load_compliance_vocab`, `load_deontic_cues`, `load_deontic_cue_map`,
`deontic_type_of`, `load_actor_synonyms`, `load_claim_type_criteria`, `load_actor_role_criteria`,
`load_operative_rubric`, `load_role_domains`, `load_section_overrides`. Everything else (`load_contract_ontology`,
`load_shapes_graph`, `reference_pack_ttl`, `load_typed_edges`, `load_template_fields`,
`load_residual_role_criteria`, ...) is in `packs.contracts.ontology.loader`.

**Data files.** `contract_bridge.ttl` -> `src/rag_wright/packs/contracts/ontology/`; `compliance_bridge.ttl` ->
`src/rag_wright/packs/compliance/ontology/`; the FTC regulation pack `ftc_16cfr255.ttl` ->
`src/rag_wright/packs/compliance/ontology/packs/`; `dim_fleet.json` -> `src/rag_wright/packs/contracts/spans/`; the
pack skills (`claim_extraction`, `compliance_judgment`, `generic_compliance_judgment`, `requirement_extraction` ->
`packs/compliance/skills/`; `extraction_semantic_judge`, `corpus_ingest` -> `packs/contracts/skills/`). The generic
skills (`generation`, `rlm`, `vision_to_text`, `span_relevance_judgment`, `okf_navigate`) stayed in
`rag_wright/skills/`. Use `reference_pack_ttl()` rather than a hardcoded path to the contract `.ttl`.

**Import contracts.** "Generic engine is domain-free": every package outside `rag_wright.packs` may not import
`rag_wright.packs`; and `rag_wright.packs.contracts` may not import `rag_wright.packs.compliance`.

**Module moves** (old -> new; the `rag_wright.` prefix is omitted):

| Before | After |
|---|---|
| `spans.boundary` | `packs.contracts.spans.boundary` |
| `spans.clause_function_classifier` | `packs.contracts.spans.clause_function_classifier` |
| `spans.clause_kg_extractor` | `packs.contracts.spans.clause_kg_extractor` |
| `spans.cuad_labels` | `packs.contracts.spans.cuad_labels` |
| `spans.dim_classifier` | `packs.contracts.spans.dim_classifier` |
| `spans.function_classifier` | `packs.contracts.spans.function_classifier` |
| `spans.function_families` | `packs.contracts.spans.function_families` |
| `spans.hybrid_classifier` | `packs.contracts.spans.hybrid_classifier` |
| `spans.legalbert_classifier` | `packs.contracts.spans.legalbert_classifier` |
| `spans.model_capabilities` | `packs.contracts.spans.model_capabilities` |
| `spans.new_function_labels` | `packs.contracts.spans.new_function_labels` |
| `spans.property_extractor` | `packs.contracts.spans.property_extractor` |
| `spans.property_grounding` | `packs.contracts.spans.property_grounding` |
| `spans.reclassify` | `packs.contracts.spans.reclassify` |
| `spans.residual_candidates` | `packs.contracts.spans.residual_candidates` |
| `spans.scarce_function_labels` | `packs.contracts.spans.scarce_function_labels` |
| `spans.segment` | `packs.contracts.spans.segment` |
| `spans.semantic_judge` | `packs.contracts.spans.semantic_judge` |
| `spans.symbolic_validation` | `packs.contracts.spans.symbolic_validation` |
| `spans.tag_clause_extractor` | `packs.contracts.spans.tag_clause_extractor` |
| `subgraphs.async_ingestion` | `packs.contracts.subgraphs.async_ingestion` |
| `subgraphs.compliance_check` | `packs.compliance.subgraphs.compliance_check` |
| `subgraphs.compliance_ingestion` | `packs.compliance.subgraphs.compliance_ingestion` |
| `subgraphs.contract_ingestion_pipeline` | `packs.contracts.subgraphs.contract_ingestion_pipeline` |
| `subgraphs.intra_document_qa` | `packs.contracts.subgraphs.intra_document_qa` |
| `subgraphs.query_constraint_extraction` | `packs.contracts.subgraphs.query_constraint_extraction` |
| `subgraphs.relational_qa` | `packs.contracts.subgraphs.relational_qa` |
| `subgraphs.requirement_extraction` | `packs.compliance.subgraphs.requirement_extraction` |
| `subgraphs.typed_clause_extraction` | `packs.contracts.subgraphs.typed_clause_extraction` |
| `subgraphs.typed_property_retrieval` | `packs.contracts.subgraphs.typed_property_retrieval` |
| `capabilities.assertion_extraction` | `packs.compliance.capabilities.assertion_extraction` |
| `capabilities.claim_extraction` | `packs.compliance.capabilities.claim_extraction` |
| `capabilities.clause_exception_linking` | `packs.contracts.capabilities.clause_exception_linking` |
| `capabilities.compliance_judgment` | `packs.compliance.capabilities.compliance_judgment` |
| `capabilities.compliance_store` | `packs.compliance.capabilities.compliance_store` |
| `capabilities.contract_kg_serve` | `packs.contracts.capabilities.contract_kg_serve` |
| `capabilities.contract_kg_store` | `packs.contracts.capabilities.contract_kg_store` |
| `capabilities.dg_extraction` | `packs.contracts.capabilities.dg_extraction` |
| `capabilities.graph_extraction` | `packs.contracts.capabilities.graph_extraction` |
| `capabilities.highlight_serve` | `packs.contracts.capabilities.highlight_serve` |
| `capabilities.property_boosted_retrieval` | `packs.contracts.capabilities.property_boosted_retrieval` |
| `capabilities.query_function_classifier` | `packs.contracts.capabilities.query_function_classifier` |
| `capabilities.query_understanding` | `packs.contracts.capabilities.query_understanding` |
| `capabilities.requirement_extraction` | `packs.compliance.capabilities.requirement_extraction` |
| `contracts.compliance` | `packs.compliance.schemas.compliance` |
| `contracts.contract_meta` | `packs.contracts.schemas.contract_meta` |
| `contracts.function` | `packs.contracts.schemas.function` |
| `contracts.function_routing` | `packs.contracts.schemas.function_routing` |
| `contracts.highlight` | `packs.contracts.schemas.highlight` |
| `contracts.jurisdiction` | `packs.contracts.schemas.jurisdiction` |
| `contracts.ontology` | `packs.contracts.schemas.ontology` |
| `contracts.property` | `packs.contracts.schemas.property` |
| `contracts.query_intent` | `packs.contracts.schemas.query_intent` |
| `contracts.value_match` | `packs.contracts.schemas.value_match` |
| `ontology._generated_template_meta` | `packs.contracts.ontology._generated_template_meta` |
| `ontology._generated_vocab` | `packs.contracts.ontology._generated_vocab` |
| `ontology.clause_template` | `packs.contracts.ontology.clause_template` |
| `ontology.codegen` | `packs.contracts.ontology.codegen` |
| `ontology.contract_taxonomy` | `packs.contracts.ontology.contract_taxonomy` |
| `ontology.derive` | `packs.contracts.ontology.derive` |
| `ontology.loader` | `packs.contracts.ontology.loader` |
| `ontology.template_introspect` | `packs.contracts.ontology.template_introspect` |
| `corpus.cuad` | `packs.contracts.corpus.cuad` |
| `corpus.cuad_ingestion` | `packs.contracts.corpus.cuad_ingestion` |
| `corpus.edgar` | `packs.contracts.corpus.edgar` |
| `corpus.gcs_ingestion` | `packs.contracts.corpus.gcs_ingestion` |
| `corpus.selection` | `packs.contracts.corpus.selection` |
| `mcp` | `packs.contracts.mcp` |
| `mcp.compliance_server` | `packs.compliance.mcp.compliance_server` |
| `mcp.intra_document_qa_server` | `packs.contracts.mcp.intra_document_qa_server` |
| `mcp.relational_qa_server` | `packs.contracts.mcp.relational_qa_server` |
| `mcp.session_store` | `packs.contracts.mcp.session_store` |
| `mcp.typed_property_retrieval_server` | `packs.contracts.mcp.typed_property_retrieval_server` |
| `skills.claim_extraction` | `packs.compliance.skills.claim_extraction` |
| `skills.compliance_judgment` | `packs.compliance.skills.compliance_judgment` |
| `skills.corpus_ingest` | `packs.contracts.skills.corpus_ingest` |
| `skills.extraction_semantic_judge` | `packs.contracts.skills.extraction_semantic_judge` |
| `skills.generic_compliance_judgment` | `packs.compliance.skills.generic_compliance_judgment` |
| `skills.requirement_extraction` | `packs.compliance.skills.requirement_extraction` |
| `reference.compliance` | `packs.compliance.invokers` |
| `reference.contract_seam` | `packs.reference_seam` |
| `reference.pack` | split into `packs.contracts.pack` + `packs.compliance.pack` (above) |

## ING-8d: generic option and span field names

**Options.** `IngestOptions` keeps only the generic `tuning`. A domain pack's knobs travel in the new
`EngineOptions.packs` mapping, keyed by the pack's name; the engine passes it through untouched.

| Before | After |
|---|---|
| `EngineOptions(ingest=IngestOptions(classify_concurrency=, clause_concurrency=, affiliations=, function_classifier=))` | `EngineOptions(packs={"contracts": ContractIngestOptions(classify_concurrency=, clause_concurrency=, affiliations=, function_classifier=)})`; `ContractIngestOptions` is in `rag_wright.packs.contracts.options` (a wrong type under `"contracts"` raises `TypeError`) |
| `IngestOptions.list_model`, `IngestOptions.clause_samples` | removed (they were accepted and never used) |
| `aproduction_document_ingest(..., list_model=, samples=)` | removed parameters |

The env fallbacks (`CLASSIFY_CONCURRENCY`, `CLAUSE_CONCURRENCY`, `RAG_INGEST_AFFILIATIONS`, `RAG_FUNCTION_CLASSIFIER`)
are unchanged. `RAG_INGEST_LIST_MODEL` / `RAG_INGEST_CLAUSE_SAMPLES` / `RAG_INGEST_CLAUSE_EXTRACTOR` are read only by
the contracts pack's legacy tag-parse extractor (not the default pipeline).

**Span fields** (new names only, no dual read). `SpanRecord` and the `Span` hook contract now forbid unknown fields,
so an old keyword argument raises a `ValidationError` instead of being silently dropped.

| Before | After |
|---|---|
| `SpanRecord.contract_id`, stored `Span.contract_id` | `document_id` |
| `SpanRecord.function`, stored `Span.function` | `primary_tag` |
| `SpanRecord.functions`, stored `Span.functions` (JSON list) | `tags` (JSON list, primary-first) |
| `SpanRecord.parent_okf_path`, `Span.parent_okf_path` (hook contract), stored `Span.parent_okf_path` | removed from the engine; the store no longer declares or writes it (existing values are left in place) |
| `to_span_record(op, contract_id=, function=, functions=, parent_okf_path=)` | `to_span_record(op, document_id=, primary_tag=, tags=)` |
| `segment_clause(..., parent_okf_path=)` (contracts pack) | parameter removed |
| `ArcadeDBStore.span_hybrid_search(..., function=)` | `span_hybrid_search(..., primary_tag=)` |
| rows from `span_hybrid_search` / `span_dense_search`: `span_id, parent_chunk_id, parent_okf_path, function` | `span_id, parent_chunk_id, primary_tag` |
| rows from `spans_by_contract` / `all_spans_by_contract` / `api.span_positions`: `function`, `contract_id` | `primary_tag`, `document_id` (`parent_okf_path` dropped) |

`clause_positions` rows keep their `contract_id` key (a contracts-pack row; it now reads `Span.document_id`).

**Existing databases.** `ArcadeDBStore.ensure_schema()` (and so `open_workspace`) raises a `RuntimeError` on a database
whose `Span` type still declares `contract_id` / `function` / `functions`, naming the fix. Migrate each existing
database once, in place:

```
uv run python -u scripts/migrate_span_fields.py <database>     # X/N progress; idempotent
```

(or `ArcadeDBStore.migrate_span_fields()`): it copies the values to the new fields in batches, removes the old fields
and drops the old properties. The legacy `parent_okf_path` values stay (the ACORD-era eval scripts read them).

## ING-8e: no domain methods or types on the generic store

`ArcadeDBStore` no longer knows a contract or compliance type. The methods moved to the packs' store extensions,
which wrap the generic store (`ContractKGStore(store)`, `ComplianceStore(store)`); each ensures its own pack schema on
construction.

| Before (`ArcadeDBStore`) | After |
|---|---|
| `contract_by_id(id)`, `all_contracts()`, `clauses_in_contract(id)`, `clause_positions(functions)`, `write_clause_exception_links(links)`, `clear_property_graph()`, `property_graph_counts()`, `clause_property_values(clause_id)` | same names on `ContractKGStore` (`rag_wright.packs.contracts.capabilities.contract_kg_store`) |
| `ensure_compliance_schema()`, `all_requirements(sources=)`, `requirement_sources()`, `ingested_citations(source)` | same names on `ComplianceStore` (`rag_wright.packs.compliance.capabilities.compliance_store`); constructing it ensures the schema |
| `spans_by_contract(contract_id, functions)` | `spans_by_document(document_id, primary_tags)` (generic store) |
| `all_spans_by_contract(contract_id)` | `all_spans_by_document(document_id)` (generic store); `ContractKGStore.all_spans_by_contract` remains as the pack's reader |
| constants `CLAUSE_TYPE`, `PROPVALUE_TYPE`, `PROPERTY_EDGE_TYPE`, `CONTRACT_TYPE`, `IS_EXCEPTION_TO_EDGE_TYPE`, helper `_property_value_key` in `rag_wright.store.arcadedb` | `rag_wright.packs.contracts.capabilities.contract_kg_store` |
| constant `REQUIREMENT_TYPE` in `rag_wright.store.arcadedb`; the `Requirement` property types in a Python table | `rag_wright.packs.compliance.capabilities.compliance_store.REQUIREMENT_TYPE`; the type is declared in `compliance_bridge.ttl` (`cmp:RequirementNode`, engine pack-schema vocabulary) and created by `ensure_pack_schema` |

`ingested_citations` / `requirement_sources` now read through the generic `kg_read(distinct=...)`. Existing compliance
databases need no migration: the `Requirement` type and its properties are unchanged.
