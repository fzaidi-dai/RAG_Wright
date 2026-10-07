# Ingesting a new corpus into the contract KG

The repeatable process for pointing the pipeline at a new corpus and getting a populated, connected KG. This is
the recipe behind the contracts pack's `corpus_ingest` skill
(`src/rag_wright/packs/contracts/skills/corpus_ingest/SKILL.md`).

The design that makes this cheap: a single **contract ingestion pipeline** (LG-3d, `contract_ingestion_pipeline`,
built on the engine's shared ingestion stages) plus a thin per-corpus **`CorpusAdapter`**. Adding a corpus is one adapter — never a re-implemented
`ingest_xyz()`. See ADR-0033 (unified KG), HYG-1 (canonical identity), ADR-0037 (the clause template is
authoritative code), and `src/rag_wright/packs/contracts/subgraphs/contract_ingestion_pipeline.py`.

## A new *contract* corpus (the common case) — 3 steps

### 1. Write one `CorpusAdapter` — the ONLY new code
Implement `documents() -> Iterable[SourceDocument]`. It owns everything corpus-specific:
- enumerate the corpus's files/records;
- **parse each to text** (PDF → docling; JSON → read; …) — parsing lives in the adapter, so the pipeline is
  parse-agnostic;
- assign the canonical id via `canonical_source_doc_id(...)` (HYG-1) — this is what lets the new corpus's
  clauses, spans, entities, and contracts share one id scheme and auto-connect;
- optionally attach corpus metadata on `SourceDocument.metadata` (e.g. annotated parties, pre-segmented spans).

`CuadAdapter` (in `packs/contracts/corpus/cuad_ingestion.py`, with its driver `arun_cuad_ingestion`) is the reference
implementation.

### 2. Point the entity registry at the corpus's parties
Extend the EDGAR verified registry (`build_verified_registry` in
`rag_wright.packs.contracts.capabilities.dg_extraction`) for the corpus's public companies, or accept
`UNLINKED` / `PRIVATE` for parties not in the registry (the honest closed-world gap).

### 3. Run it
```python
from rag_wright.packs.contracts.capabilities.contract_kg_store import ContractKGStore
from rag_wright.packs.contracts.subgraphs.contract_ingestion_pipeline import (
    aproduction_document_ingest, arun_corpus_ingestion)

report = await arun_corpus_ingestion(
    YourAdapter(path),
    aproduction_document_ingest(store, cache_dir=..., registry=...),
    is_done=lambda doc: ContractKGStore(store).contract_by_id(doc.source_doc_id) is not None,   # optional resume-skip
)
```
Both are async (the sync `run_corpus_ingestion` / `production_document_ingest` and the KG-7 `party_clause_linking`
step no longer exist; the party-to-clause link was retired with the `PartyTo` edge, ADR-0091). The generic
pipeline does the rest, per document:
`chunk → segment (legal segmenter + SetFit function soft-tags) → group into provisions (uncertain boundaries to the
decision model) → clause-extract ∥ index spans ∥ graph-extract → entity_resolution → write (clause KG + entity
graph + Contract and Document nodes)`. A bad document dead-letters and is skipped; the run streams `X/N` progress.

### Reused unchanged (no edits)
The clause extraction template (contract domain model), the SetFit function classifier (contract taxonomy; the
default, `RAG_FUNCTION_CLASSIFIER=llm` reverts to the LLM classifier), the property classifier fleet, the pipeline,
the canonical identity scheme. **No `ingest_xyz()`, no template edit, no retrain.**

## A new *domain* (non-contract)
Do not adapt this contract pipeline. Use the engine's generic builder (ADR-0124): `build_ingestion(extractor, ...)`
from `rag_wright.api`, then `.aingest(ws, sources, cache_dir=...)`. The engine owns parse, chunk, segment, index,
group and write (with docling-layout segmentation and structural unit grouping as defaults); your domain supplies
the `extractor` (a unit in, typed `KgNode`/`KgEdge` records out) and overrides only the hooks it needs (segmenter,
span tagger, unit grouper, boundary decider, writer, `document_hook`). Check the defaults on your own sample files
with `evaluate_ingestion(sources, cache_dir=...)` and tune them through `IngestionTuning`. You may still need a new
ontology pack, a classifier for your own taxonomy, and possibly a different entity registry. See
[concepts](concepts.md#ingestion-the-engines-pipeline-the-domains-extractor-adr-0124) and
[KG construction](domain-adaptation/kg-construction.md).

## Honest caveats
- **Party extraction latency** (INGEST-GRAPH-LATENCY): the async party extraction (`aextract_parties`) is bounded by
  the model seam's wall-clock deadline (180 s per logical call), so a stalled call is cancelled rather than hanging
  the run; the graph step retries, and a document that keeps failing is dead-lettered while the run goes on.
- **Extraction cost/idempotence**: clause extraction is cached per provision, keyed by the clause id (document,
  index, content hash) + the anchor span + the function + the template version, which includes the extraction
  method (decision-model or LLM judge and residual lane). Re-runs and template or method changes re-extract only
  what they must. Boundary decisions are cached too.
