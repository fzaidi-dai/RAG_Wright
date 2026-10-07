# Ingestion hooks: a generic, domain-neutral ingestion builder (ING-*)

Status ledger for the workstream that removes the contract-domain leak from ingestion (ADR-0124). Each task runs
the working loop (contract → red → green → verify → approval gate → one commit).

**Why.** Today the reference ingestion pipeline hardcodes three domain decisions inside its stage functions: the
SEGMENTER (`segment_clause`, legal markers and abbreviations, fused with the contract function classifier), the
UNIT (spans grouped into contract *provisions* by `Section/Article/Clause N` headings + a contract-trained boundary
decider + a legal furniture filter), and the extractor. A new domain's extractor therefore receives a contract
provision as its unit, and the only seam (`abuild_document_ingest`, seven stage functions) is internal. Engine gaps
G3/G4 in `docs/domain-adaptation/_engine-gaps.md`.

**Target.** One public builder in `rag_wright.api` where the engine owns the wiring, indexing, page provenance,
ids, writing, progress and dead-lettering, and a domain overrides only the domain-shaped hooks, each with a
domain-neutral default:

| Hook | Default (engine, domain-neutral) | Reference pack passes |
|---|---|---|
| `segmenter` | docling-layout-based NLP segmenter (headings, paragraphs, list items, tables, sentences) | `segment_clause` |
| `span_tagger` (optional) | none | the function classifier |
| `unit_grouper` | layout-structural grouping (headings), else the chunk | provision grouping + legal furniture filter |
| `boundary_decider` (optional) | none (uncertain → continue) | the Jev decider |
| `extractor` | required, domain-supplied | the clause extractor |
| `writer` (optional) | `kg_write` of the extractor's `KgNode`/`KgEdge` | the typed clause-record writer |

## Tasks

| id | what | verify | status |
|---|---|---|---|
| ING-1 | ADR-0124 + the hook contracts (`LayoutItem`, `Span`, `TaggedSpan`, `Unit`, `UnitExtraction`, the hook protocols, and the engine-enforced checks `check_tiling` / `check_units` / `check_extraction`), exported from `rag_wright.api` with `KgNode`/`KgEdge` | `uv run pytest tests/contracts/test_ingestion_hooks.py` + the existing span/boundary suites green | done |
| ING-2 | The docling-layout NLP segmenter as the default (eval first: a mixed legal + non-legal gold set; tiling + boundary quality) | `uv run pytest tests/ingestion` + `RAG_EVAL_TEXTILE_DIR=… uv run python -u eval/segmenter_eval.py` (gate PASS); live on real client PDF/XLSX + a public PDF | done |
| ING-3 | The generic layout-structural unit grouper as the default; the provision grouping + legal furniture filter + Jev decider move into the reference pack (patterns as pack data, ADR-0066) | its eval + unit tests | todo |
| ING-4 | The public `build_ingestion` builder; the contract pipeline re-expressed as the reference pack's instantiation of it | byte-identical parity test (spans + jobs) on the existing fixtures; LIVE contract re-ingest (zero regression) + LIVE non-legal ingest | todo |
| ING-5 | Docs + skill: "Identifying your domain's units" + the stage-ownership table in `kg-construction.md`; new skill `building-an-ingestion-capability`; links from `authoring-capabilities.md`, the domain-adaptation README, `using-the-rag-wright-engine`, the product-starter templates, CLAUDE.md; G3/G4 resolved in the gaps register | doc link check; skill conformance | todo |
