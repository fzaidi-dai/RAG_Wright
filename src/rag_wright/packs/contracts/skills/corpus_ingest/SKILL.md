---
name: corpus_ingest
description: >
  The repeatable method for ingesting ANY new corpus of contracts into the one contract KG and
  auto-connecting it (parties <-> clauses). Point the GENERIC ingestion pipeline
  (contract_ingestion_pipeline) at the corpus through a single thin CorpusAdapter -- never a
  re-implemented ingest_xyz(). Write the adapter (parse + canonical source_doc_id + optional metadata),
  point the entity registry at the corpus's parties, and run run_corpus_ingestion; the pipeline chunks,
  segments, function-classifies, extracts clauses + the party graph, resolves entities, and writes both. Applied over src/rag_wright/packs/contracts/subgraphs/contract_ingestion_pipeline.py.
---

# Ingesting a new corpus into the contract KG

This skill teaches a **method**, not a behavior. The design that makes it cheap: ONE generic, corpus-agnostic
ingestion pipeline (LG-3d, `contract_ingestion_pipeline`) plus a thin per-corpus **`CorpusAdapter`**. Adding a
corpus is one adapter — **never** a re-implemented `ingest_xyz()` that duplicates the flow. See ADR-0033
(unified KG), HYG-1 (canonical identity), ADR-0037 (the clause template is authoritative code), and
`docs/corpus_ingest_recipe.md` (the prose recipe this skill formalizes).

## The method: one generic pipeline + one thin adapter

The pipeline is fixed and shared. Everything corpus-specific lives behind one seam,
`CorpusAdapter.documents() -> Iterable[SourceDocument]`. `SourceDocument` is `{source_doc_id, text, metadata}`.
The pipeline, per document, runs: **chunk (semantic_chunking) → segment → LegalBERT function-classify →
clause-extract ∥ graph-extract → entity_resolution → write (clause KG + entity graph)**. (The KG-7
`party_clause_linking`/PartyTo post-step was retired — issue 0028 / ADR-0091 — since party→clause is reached
via CONTRACTS_WITH provenance + the contract-scoped clause KG.)

## A new *contract* corpus — 3 steps

### 1. Write one `CorpusAdapter` — the ONLY new code
`documents()` owns everything corpus-specific:
- enumerate the corpus's files/records;
- **parse each to text** (PDF → docling; JSON → read; …) — parsing lives here, so the pipeline is parse-agnostic;
- assign the id via `canonical_source_doc_id(...)` (HYG-1) — this is **load-bearing**: it is what lets the new
  corpus's clauses, spans, entities, and contracts share ONE id scheme and auto-connect. A slug that disagrees
  with the rest (e.g. `-` for spaces instead of `_`) silently breaks the cross-graph join;
- optionally attach corpus quirks on `SourceDocument.metadata` (annotated parties, pre-segmented spans, …).

`CuadAdapter` (in `contract_ingestion_pipeline.py`) is the reference implementation.

### 2. Point the entity registry at the corpus's parties
Extend the EDGAR verified registry (`build_verified_registry`) for the corpus's public companies, or accept
`UNLINKED` / `PRIVATE` for parties not in the registry (the honest closed-world gap).

### 3. Run it (monitored)

```python
from rag_wright.packs.contracts.subgraphs.contract_ingestion_pipeline import (
    arun_corpus_ingestion, aproduction_document_ingest,
)

report = await arun_corpus_ingestion(
    YourAdapter(path, limit=N),                 # test on a FEW docs first; never a full re-ingest without intent
    aproduction_document_ingest(store, cache_dir=..., registry=...),
)
# report: documents_ingested, dead_lettered (per-doc), per_document
```

The ingest **extraction models are caller-configurable** (like the query/compliance entrypoints; env vars stay
the fallback): `aproduction_document_ingest(store, cache_dir=..., registry=..., extract_model=...)`. `extract_model` is the primary clause-property model (an `ExtractionModel` or a bare model-id
string; default = the `RAG_SERVING` backend model). `graph_extract_model` sets the party+affiliation extraction model (they share one),
`judge_model` the ingest semantic-judge model, and `chunk_model` the chunker's boundary-refinement model (used
only for over-cap sections). All accept a bare id or an `ExtractionModel` and default to their backend/env value,
so every ingest LLM surface (clause extract, party/affiliation, judge, chunk boundary, and the
pre-existing `classify_fn`) is now a call-site argument.

`run_corpus_ingestion` streams `X/N` progress; a bad document dead-letters and is skipped (one bad doc never
kills the corpus). Write to a SCRATCH database first (`from_env(database=..., reset=True)`) to keep it
non-destructive while proving it out.

## Rules (not optional)

1. **Never write an `ingest_xyz()` that re-implements the flow.** A new corpus = one `CorpusAdapter`, then
   `run_corpus_ingestion(adapter, ...)`. If you find yourself copying the pipeline, stop.
2. **The canonical `source_doc_id` is load-bearing.** Always derive it via `canonical_source_doc_id`; a
   divergent slug breaks the cross-graph join (HYG-1). Every graph must share one id scheme.
3. **Extraction is concurrent, per-item tolerant, and cached.** Clause/graph extraction runs under
   `map_concurrent` (granite is ~10s/single call); a truncated/failed span is SKIPPED, not fatal to the
   document; and each successful extraction is cached by clause-id + template-schema-version, so a re-run or a
   template change re-extracts only what it must.
4. **Long-running runs stream `X/N` and are actively monitored** (CLAUDE.md) — never launch-and-forget.
5. **The clause template is authoritative code, not regenerated** (ADR-0037); tune extraction by editing
   `clause_template.py`, never by chasing a regeneration from the `.ttl`/spec.

## A new *domain* (non-contract)
The pipeline *structure* stays; the capabilities it binds change: a **new extraction template** (bootstrap a
fresh `.py` from a new ontology, then hand-maintain it — ADR-0037), a **retrained/replaced function classifier**
(new taxonomy), and possibly a different entity registry.

## What this skill does NOT own (deferred to the pipeline / capabilities)
- **The pipeline internals** (`build_document_ingest` graph, dead-letter, the extraction seams) — LG-3d.
- **The extraction capabilities** — semantic_chunking, the function classifier, clause extraction (docling-graph
  + granite, ADR-0037 template), GP-1B graph_extraction (ADR-0035), entity_resolution.
- **The span/embedding retrieval index** — wired into the pipeline as a parallel `index_spans` branch off the
  shared `segment` node (INGEST-REFACTOR phase 2a); a corpus now gets the typed KG + entity graph + the
  dense/sparse retrieval index in one pass. Indexing is best-effort (a failed index degrades to 0 spans, never
  dead-letters the document's KG).

The method is: parse behind the adapter, one canonical id, run the generic pipeline, connect once. Keep this
file about that shape; the pipeline supplies the flow, the capabilities, and the tests.
