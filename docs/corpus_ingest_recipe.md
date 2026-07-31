# Ingesting a new corpus into the contract KG

The repeatable process for pointing the pipeline at a new corpus and getting a populated, connected KG. This is
the draft recipe that **SKILL-corpus-ingest** will formalize into a `SKILL.md`.

The design that makes this cheap: a single **generic ingestion pipeline** (LG-3d, `contract_ingestion_pipeline`)
plus a thin per-corpus **`CorpusAdapter`**. Adding a corpus is one adapter — never a re-implemented
`ingest_xyz()`. See ADR-0033 (unified KG), HYG-1 (canonical identity), ADR-0037 (the clause template is
authoritative code), and `src/rag_wright/subgraphs/contract_ingestion_pipeline.py`.

## A new *contract* corpus (the common case) — 3 steps

### 1. Write one `CorpusAdapter` — the ONLY new code
Implement `documents() -> Iterable[SourceDocument]`. It owns everything corpus-specific:
- enumerate the corpus's files/records;
- **parse each to text** (PDF → docling; JSON → read; …) — parsing lives in the adapter, so the pipeline is
  parse-agnostic;
- assign the canonical id via `canonical_source_doc_id(...)` (HYG-1) — this is what lets the new corpus's
  clauses, spans, entities, and contracts share one id scheme and auto-connect;
- optionally attach corpus metadata on `SourceDocument.metadata` (e.g. annotated parties, pre-segmented spans).

`CuadAdapter` (in `contract_ingestion_pipeline.py`) is the reference implementation.

### 2. Point the entity registry at the corpus's parties
Extend the EDGAR verified registry (`build_verified_registry`) for the corpus's public companies, or accept
`UNLINKED` / `PRIVATE` for parties not in the registry (the honest closed-world gap).

### 3. Run it
```python
report = run_corpus_ingestion(
    YourAdapter(path),
    production_document_ingest(store, cache_dir=..., registry=...),
    link_fn=lambda: len(party_clause_linking(store).links),
)
```
The generic pipeline does the rest, per document:
`chunk (semantic_chunking) → segment → LegalBERT function-classify → clause-extract ∥ graph-extract →
entity_resolution → write (clause KG + entity graph)`, then `party_clause_linking` (KG-7) runs once to connect
parties to clauses. A bad document dead-letters and is skipped; the run streams `X/N` progress.

### Reused unchanged (no edits)
The clause extraction template (contract domain model), the LegalBERT function classifier (contract taxonomy),
the pipeline, the canonical identity scheme, the KG-7 link. **No `ingest_xyz()`, no template edit, no retrain.**

## A new *domain* (non-contract) — the bigger lift
The pipeline *structure* stays; the capabilities it binds change:
- a **new extraction template** (bootstrap a fresh `.py` from a new ontology, then hand-maintain it — ADR-0037);
- a **retrained/replaced function classifier** (new taxonomy);
- possibly a different entity registry.

## Honest caveats (phase-2 work, not yet wired)
- The **span/embedding retrieval index** is not in the generic `write` yet (INGEST-REFACTOR phase 2). A new
  corpus currently gets the typed KG + entity graph + link, but not the dense/sparse retrieval index.
- **`extract_parties` latency** (INGEST-GRAPH-LATENCY): add a per-call timeout before running a large corpus.
- **Extraction cost/idempotence**: clause extraction is cached per span (keyed by clause id + template-schema
  version), so re-runs and template changes re-extract only what they must.
