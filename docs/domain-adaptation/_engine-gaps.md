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
  (`rag_wright.capabilities.graph_storage`). `GraphWriter` also takes the store itself, which a product only reaches
  through the workspace's internal store. `entity_resolution` and `entity_disambiguation` remain internal pipeline
  steps (ADR-0118, EP-CORE-1b-iii), not invocable by name.
- **Proposed:** export the entity-graph building blocks (or one helper that runs them over a workspace) from
  `rag_wright.api`, and/or a resolver/registry hook on `EngineConfig`. (Entity resolution itself is already
  domain-neutral and injectable per ADR-0067; this is an exposure gap, not coupling.)
- Surfaced: PREP-4.4.

### G2: The public config/env names carry contract-domain vocabulary (partly closed)
- **Closed part:** the `Clause` (and other contract) KG types are no longer in the default schema; `pack=None`
  creates only the engine types (ING-8a).
- **Closed by ING-8d:** `IngestOptions` holds only the generic `tuning`; the contract knobs moved to the pack's
  `ContractIngestOptions` (via `EngineOptions.packs["contracts"]`); the span fields are `document_id`,
  `primary_tag`, `tags` (`parent_okf_path` left the engine `Span`). The env vars `CLAUSE_CONCURRENCY`,
  `RAG_INGEST_CLAUSE_*`, `RAG_SETFIT_CLAUSE_DIR` are read only inside the contracts pack.
- **Still open:** the generic store keeps contract-shaped methods (`spans_by_contract`, `all_spans_by_contract`,
  `contract_by_id`, `clauses_in_contract`, `clause_positions`): ING-8e moves or renames them.
- Surfaced: PREP-4.1 audit.

### G5: Pack and manifest helpers are not on `rag_wright.api`
- **Where:** `load_pack`, `engine_capabilities` and `CapabilityManifest` live in `rag_wright.capabilities.manifests`;
  `register_canonical_slugs` and `canonical_capability_slugs` in `rag_wright.capabilities.registry`. None is
  re-exported from `rag_wright.api`.
- **Impact:** a product that registers capabilities or loads its own pack must import `rag_wright.capabilities.*`,
  which contradicts the product-starter rule "consume the engine only through `rag_wright.api`".
- **Proposed:** re-export them from `rag_wright.api` (as PREP-1.5 did for `register_capability`).

### G6: A domain's decision-model boundary decider has no cache or repeatability
- **Where:** `build_ingestion(boundary_decider=)` takes any async decider, but the decision cache
  (`cached_decider`) and the Jev-backed residue decider (`jev_boundary_decider`, `residue_request`) live only in the
  reference pack.
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
  the spaCy model (`uv pip install 'rag-wright[ner]'` + `spacy download`). See [installation](../installation.md).
