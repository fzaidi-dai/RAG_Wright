# Engine gaps surfaced by the domain-adaptation docs

Writing the engine docs + the domain-adaptation guide (engine-prep WS2/WS4) surfaced places where the engine's
surface still assumes the reference (contract/compliance) domain, or where a seam a new domain needs is not yet
exposed. These are **engine follow-ups**, not blockers — a new domain works today via the documented paths — but
they are the real "does a new customer benefit?" items. The genuine engine ones are cross-posted into
`docs/specs/engine-platform/TASKS.md`.

## Open (engine follow-ups)

### G1: No public seam for a domain's entity resolver / registry (partly closed)
- **Closed part:** `build_ingestion(..., document_hook=)` is a public per-document seam. The hook
  `(ws, source_document, chunks)` runs once per document after its records are written, so a domain can run its own
  entity graph and resolver there (recipe in [entity resolution](entity-resolution.md)).
- **Still open:** there is no resolver/registry hook on `EngineConfig` or `rag_wright.api`, and the entity-graph
  building blocks are not exported from `rag_wright.api`: `build_graph_extraction`
  (`rag_wright.subgraphs.graph_extraction`), `disambiguate` (`rag_wright.capabilities.disambiguation`),
  `resolve_entities` (`rag_wright.capabilities.entity_resolution`) and `GraphWriter`
  (`rag_wright.capabilities.graph_storage`), with the contracts they take (`ExtractionResult` / `EntityMention` in
  `rag_wright.contracts.extraction`, `ChunkId` / `EntityId` in `rag_wright.contracts.identifiers`) and the registry
  (`EntityRegistry` / `RegistryRecord` in `rag_wright.ontology.registry`). `GraphWriter` also takes the store
  itself, which a product only reaches through the workspace's internal store. `entity_resolution` and `entity_disambiguation` remain internal pipeline
  steps (ADR-0118, EP-CORE-1b-iii), not invocable by name.
- **Proposed:** export the entity-graph building blocks (or one helper that runs them over a workspace) from
  `rag_wright.api`, and/or a resolver/registry hook on `EngineConfig`. (Entity resolution itself is already
  domain-neutral and injectable per ADR-0067; this is an exposure gap, not coupling.)
- Surfaced: PREP-4.4.

### G2: The public config/env names carry contract-domain vocabulary (closed)
- **Closed part:** the `Clause` (and other contract) KG types are no longer in the default schema; `pack=None`
  creates only the engine types (ING-8a).
- **Closed by ING-8d:** `IngestOptions` holds only the generic `tuning`; the contract knobs moved to the pack's
  `ContractIngestOptions` (via `EngineOptions.packs["contracts"]`); the span fields are `document_id`,
  `primary_tag`, `tags` (`parent_okf_path` left the engine `Span`). The env vars `CLAUSE_CONCURRENCY`,
  `RAG_INGEST_CLAUSE_*`, `RAG_SETFIT_CLAUSE_DIR` are read only inside the contracts pack.
- **Closed by ING-8e:** the generic store no longer carries contract- or compliance-shaped methods or types: the
  contract reads/writes live on `ContractKGStore`, the Requirement schema (declared in `compliance_bridge.ttl`) and
  reads on `ComplianceStore`, and the span reads are per document (`spans_by_document`, `all_spans_by_document`).
- Surfaced: PREP-4.1 audit.

### G5: Pack and manifest helpers are not on `rag_wright.api` (closed)
- `CapabilityManifest`, `load_pack`, `engine_capabilities`, `register_canonical_slugs` and
  `canonical_capability_slugs` are re-exported from `rag_wright.api` (2026-10-07; the same objects as their
  `rag_wright.capabilities.*` homes).

### G6: A domain's decision-model boundary decider has no cache or repeatability
- **Where:** `build_ingestion(boundary_decider=)` takes any async decider, but the decision cache
  (`cached_decider`) and the Jev-backed residue decider (`jev_boundary_decider`, `residue_request`) live only in the
  reference pack (`rag_wright.packs.contracts.spans.boundary`).
- **Impact:** a domain that adjudicates its boundary residue with the decision model (Jev) pays for every call again
  on a re-ingest, and its boundaries can change between runs (Jev is not repeatable call to call).
- **Proposed:** a generic `cached_decider` in the engine (keyed by the decision model, the prompt and the batch).

### G7: No generic reader for decision criteria authored in a pack `.ttl`
- **Where:** the reference pack authors its decision options and criteria in the ontology (`cbr:ResidualRole` +
  `cbr:decisionCriterion` + `cbr:roleOrder`, `cmp:decisionCriterion`), but the reader
  (`load_residual_role_criteria`) is in the reference loader `rag_wright.packs.contracts.ontology.loader`, which generic code may
  not import.
- **Impact:** a domain writes its own small `rdflib` reader for its criteria.
- **Proposed:** a generic reader in `ontology.pack_schema` (or similar) over an engine-namespaced criterion
  vocabulary.

### G8: The default record writer is not fully idempotent
- **Where:** with no `writer=`, `build_ingestion` writes with the store's `kg_write`: nodes are upserted by their key,
  but edges are created (`CREATE EDGE`), so re-ingesting a document duplicates its record edges. The store's
  idempotent `kg_ensure_edges` (used for `EmbeddedIn` / `AttachedTo`) is not exported from `rag_wright.api`.
- **Impact:** a domain whose extractor returns edges must pass its own idempotent `writer`, or clear the document's
  records before a re-ingest.
- **Proposed:** make the default writer use `kg_ensure_edges`, or export it.

### G9: One domain pack per workspace through the public API
- **Where:** `EngineConfig.pack` takes one `.ttl`. A second pack's schema needs the store-internal
  `ArcadeDBStore.ensure_pack_schema(ttl)`.
- **Proposed:** accept several packs on `EngineConfig`, or a public helper that ensures a pack's schema.

### G10: No SHACL gate in `build_ingestion`
- **Where:** the symbolic SHACL validation gate (ADR-0040 layer 2) runs only inside the reference contract pipeline.
  `build_ingestion` enforces the structural checks (`check_tiling`, `check_units`, `check_extraction`) but does not
  validate records against a pack's SHACL shapes.
- **Impact:** a domain that authors shapes validates its records itself (in its extractor or writer).
- **Proposed:** an optional SHACL validation step over the pack's shapes.

### G11: Dead reference-pipeline knobs (closed)
- `IngestOptions.list_model` / `clause_samples` and the pipeline's `list_model=` / `samples=` were removed (ING-8d).

### G12: No config route to register a decision-model profile
- **Where:** `DECISION_PROFILES` (`rag_wright.models.profiles`) holds only `jev-1.13` and `jev-latest`. An unknown id
  (from `RAG_DECISION_MODEL` or the `model` input of `jev_decision`) gets a default profile that still points at the
  OpenRouter Decisions endpoint with `OPENROUTER_API_KEY`. There is no `EngineConfig` field or env setting for a
  decision profile's endpoint or key.
- **Impact:** swapping in an on-premises decision model (Laya, or any server speaking the Decisions API) needs an
  engine code edit, or mutating the module-level dict from product code (an engine internal).
- **Proposed:** a public way to register a `DecisionModelProfile` (on `EngineConfig`, or an `rag_wright.api` helper).

### G13: Entity disambiguation is tuned for contract parties and not injectable
- **Where:** `rag_wright.capabilities.disambiguation.disambiguate` normalizes and rejects mentions with
  `rag_wright.corpus.canonicalize` (and `GraphWriter` keys unlinked nodes by the same normalizer): legal-form suffixes
  (`Inc.`, `LLC`, `GmbH`, `N.A.`, ...) are stripped, contract role phrases (`the buyer`, `collectively`, ...) and a
  fixed list of role and broad words (`buyer`, `seller`, `licensor`, `customer`, `supplier`, `services`, `global`,
  ...) are rejected as entities on their own. Its only parameter is `coreference_resolvers`. The generic
  `RegistryRecord` also carries a `ticker` field (the reference domain's SEC alias).
- **Impact:** a new domain's entity names are normalized and filtered by contract-party rules it cannot replace (a
  mention that is exactly `Customer` or `Supplier` is dropped); this is domain knowledge in Python (ADR-0066).
- **Proposed:** inject the normalizer and the reject rules (as `EntityRegistry(normalize=)` already is), with the
  current rules moved to the contracts pack as its own; generic aliases instead of `ticker`.
- Surfaced: ING-5 doc audit.

### G14: ARD publication is not on `rag_wright.api`
- **Where:** a product registers its capabilities through `rag_wright.api` (`register_capability`, `load_pack`), but
  writing a capability's ARD manifest (`urn:air`) is `rag_wright.capabilities.manifests.publish` / `publish_all`,
  which are not exported. `publish` requires the slug in `canonical_capability_slugs()`.
- **Impact:** ARD registration is part of every capability's definition of done, but a product reaches it only
  through an engine module.
- **Proposed:** export `publish` (with a target root) from `rag_wright.api`.
- Surfaced: ING-5 doc audit.

### G15: No public accessor for the workspace store (pack store extensions take `ws._store`)
- **Where:** `WorkspaceHandle` documents its store as engine-internal ("NOT a product accessor"), yet a pack's store
  extension (`ContractKGStore`, `ComplianceStore`, or a product pack's own) wraps the workspace store, and the reference
  seam builds them from `ws._store` (7 uses in `packs/reference_seam.py`); the adaptation guides do the same.
- **Impact:** a product following the reference seam reaches a private attribute; the API reference and the worked
  example contradict each other.
- **Interim rule (given to RuleWright):** `ws._store` is sanctioned only to construct a pack store extension.
- **Proposed:** a public, typed way to hand the workspace store to a pack store extension (a read-only `Store`-protocol
  accessor, or a `pack_store(ws, cls)` helper), then correct the API reference and the reference seam.
- Surfaced: RuleWright migration Q&A (2026-10-08).

### G16: No public bytes entry for document parsing (closed)
- Closed by PS-2: `parse_document_bytes(document_id, name, data, *, cache_dir, ...)` and its async twin
  `aparse_document_bytes` are exported from `rag_wright.api`, and `IngestSource(data=..., name=...)` lets
  `build_ingestion` and `evaluate_ingestion` take uploads without temp files (`name`'s extension picks the format).

### G17: `ModelRole` is required by the public API but not exported (closed)
- Closed by PS-1: `ModelRole` is exported from `rag_wright.api` (the same enum), so a product passes it to
  `ws.model_id(role)` and keys `EngineConfig.models` by its values; `model_for` / `PROFILES` stay internal.

### G18: A product cannot meter or trace its own model calls through the public API (closed)
- Closed by PS-3: `record_usage` (a product's own model call, recorded into every active `measure_usage` scope) and
  `traced_run` / `traced_step` (group engine generations with a product's run and steps in Langfuse; no-ops unless
  tracing is configured) are exported from `rag_wright.api`. The generation-level helpers stay internal.

### G19: Answer generation and relevance judgment are not invocable by a product (closed)
- Closed by PS-4: `agenerate_answer(query, evidence, *, ws)` and `ajudge_spans(spans, condition, *, ws)` are exported
  from `rag_wright.api` with their types (`EvidenceItem`, `GeneratedAnswer`, `AnswerKind`, `RelevanceVerdict`,
  `Relevance`, `Condition`); the models come from the workspace's roles (`GENERAL`, `STRUCTURED_REASONING`), and the
  judge returns final, closed-vocabulary verdicts. The query embedder stays internal: retrieval goes through the
  invokers.

### G20: Trained classifier weights are not packaged and the property fleet's location is not configurable (closed)
- Closed by PS-5: every reference-pack classifier (the property fleet, the clause-type ensemble, the query-side
  LegalBERT) loads from one models root, `RAG_MODELS_DIR` (default: the engine checkout's `data/models` when it
  exists, else `./data/models`), resolved by `rag_wright.models.weights.models_dir()`. The weights are published as
  checksummed per-model archives under `gs://dreamai-pocs-ragwright-ingest/models/reference-pack/v1/` (private) and
  fetched with `scripts/fetch_reference_models.py`. Removing them from the engine checkout is a later step.

### G21: No defined "pack SDK" surface for pack code
- **Where:** a pack's code (the reference pack today, a product's forked pack next) imports about 30 engine-internal
  modules (`contracts.*`, `models.seam` / `models.tag_structured` / `models.profiles`, `subgraphs.scaffold`,
  `store.seam`, several `capabilities.*`, `util.concurrent`) and makes 18 raw store queries (`_store._query` /
  `_store._command`). Only the seam-level surface (`rag_wright.api`) is declared stable.
- **Impact:** a product that owns its pack (the agreed direction for RuleWright) depends on engine internals; any engine
  refactor can break it without a declared contract.
- **Proposed:** define and document a stable pack-author tier (which modules and types a pack may import, with the
  same compatibility promise as `rag_wright.api`), give pack store extensions a query primitive instead of raw SQL, and
  enforce the boundary for pack code the way the import contracts enforce it for generic code.
- Progress: PS-6 added the query primitives (`kg_count`, `kg_delete`, `kg_update`, and `key_range` on `kg_read`, on
  the `Store` protocol and `rag_wright.api`); the pack-SDK tier and the reference pack's move onto it are PS-7/PS-8.
- Surfaced: RuleWright migration Q&A (2026-10-08).

## Resolved during engine-prep (for the record)

- **spaCy is an optional runtime asset**, not a hard/direct-URL dependency — the publish blocker is gone
  (PREP-1.1, ADR-0121).
- **`register_capability` / `load_reference_pack` / `reference_pack` re-exported from `rag_wright.api`** so the
  public surface is uniformly `rag_wright.api` (PREP-1.5).
- **`intra_document_qa` abstained on every document** — a three-bug chain (provision boundary, clause `span_id`,
  the `ContractKGStore.all_spans_by_contract` serve regression) — fixed with tests + a live cited answer
  (PREP-2.5 diversion, ADR-0122).
- **G3/G4: the ingestion seam was internal (`abuild_document_ingest`) and the extraction unit was a contract
  provision.** Closed by ING-1 to ING-4b: the public `build_ingestion` with hooks and domain-neutral defaults
  (ADR-0124).
- **Neutral default schema** (ING-8a): `pack=None` creates only the engine types (`Chunk`, `Span`, `Entity`,
  `Relationship`, `Mentions`, `Document`, `EmbeddedIn`, `AttachedTo`).
- **No generic module imports the reference pack** (ING-8b), enforced by the import-linter contract
  (`tests/arch/test_import_contracts.py`).
- **`CANONICAL_CAPABILITY_SLUGS` replaced by `canonical_capability_slugs()`**: the engine's own slugs plus those each
  loaded pack adds with `register_canonical_slugs` (ING-8b).
- **Spreadsheets and attachments:** hidden sheets are ingested by default (ING-4a); files embedded in Office
  documents (ING-6) and files attached to a PDF (ING-6b) become linked child documents.
- **Table rows:** `table_rows(source_document)` returns every parsed table's rows as exact cells (`TableRow`), and a
  one-row unit carries its row as `Unit.table_row` (ING-7).

## Prerequisites a domain provides (not engine gaps)

- **ArcadeDB** (the store), **a model provider** (OpenRouter or self-hosted vLLM), and — only for the NER path —
  the spaCy model (`uv pip install 'rag-wright[ner]'` + `uv run python -m spacy download en_core_web_sm`). See
  [installation](../installation.md).
