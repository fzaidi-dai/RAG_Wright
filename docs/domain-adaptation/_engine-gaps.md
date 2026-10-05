# Engine gaps surfaced by the domain-adaptation docs

Writing the engine docs + the domain-adaptation guide (engine-prep WS2/WS4) surfaced places where the engine's
surface still assumes the reference (contract/compliance) domain, or where a seam a new domain needs is not yet
exposed. These are **engine follow-ups**, not blockers — a new domain works today via the documented paths — but
they are the real "does a new customer benefit?" items. The genuine engine ones are cross-posted into
`docs/specs/engine-platform/TASKS.md`.

## Open (engine follow-ups)

### G1 — No public seam for a domain's entity resolver / registry
- **Where:** `entity_resolution` and `entity_disambiguation` are **internal pipeline steps** (ADR-0118,
  EP-CORE-1b-iii), not invocable-by-name; `rag_wright.api` and `EngineConfig` have **no hook** to supply a domain's
  `EntityResolver` / `EntityRegistry`.
- **Impact:** a new domain must wire its resolver inside its own ingestion subgraph (the capability-authoring path —
  so it works), but there is no first-class "bring your resolver" configuration.
- **Proposed:** a resolver/registry hook on `EngineConfig`, or an `rag_wright.api` helper to register a domain
  resolver. (ER itself is already domain-neutral + injectable per ADR-0067 — this is an *exposure* gap, not coupling.)
- Surfaced: PREP-4.4.

### G2 — The public config/env names carry contract-domain vocabulary
- **Where:** `IngestOptions.clause_concurrency` / `clause_samples`; env `CLAUSE_CONCURRENCY` / `RAG_INGEST_CLAUSE_*`
  / `RAG_SETFIT_CLAUSE_DIR`; and the `Clause` / `function` KG vertex types in the generic surface.
- **Impact:** a non-contract domain sees contract vocabulary in the engine's *public* API/env — reads as if the
  engine is a contracts tool.
- **Proposed:** generic aliases (e.g. a unit-oriented name) with the contract names kept as the reference pack's,
  or a documented mapping. These are **field renames (engine changes)**, not doc edits — the docs were kept
  domain-neutral around them.
- Surfaced: PREP-4.1 audit.

## Resolved during engine-prep (for the record)

- **spaCy is an optional runtime asset**, not a hard/direct-URL dependency — the publish blocker is gone
  (PREP-1.1, ADR-0121).
- **`register_capability` / `load_reference_pack` / `reference_pack` re-exported from `rag_wright.api`** so the
  public surface is uniformly `rag_wright.api` (PREP-1.5).
- **`intra_document_qa` abstained on every document** — a three-bug chain (provision boundary, clause `span_id`,
  the `ContractKGStore.all_spans_by_contract` serve regression) — fixed with tests + a live cited answer
  (PREP-2.5 diversion, ADR-0122).

## Prerequisites a domain provides (not engine gaps)

- **ArcadeDB** (the store), **a model provider** (OpenRouter or self-hosted vLLM), and — only for the NER path —
  the spaCy model (`uv pip install 'rag-wright[ner]'` + `spacy download`). See [installation](../installation.md).
