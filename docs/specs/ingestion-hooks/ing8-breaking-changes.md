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
