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
