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

Order: ING-1 .. ING-4c, ING-6, ING-7, then ING-5 (docs + skill cover everything). ING-3b is independent.

| id | what | verify | status |
|---|---|---|---|
| ING-1 | ADR-0124 + the hook contracts (`LayoutItem`, `Span`, `TaggedSpan`, `Unit`, `UnitExtraction`, the hook protocols, and the engine-enforced checks `check_tiling` / `check_units` / `check_extraction`), exported from `rag_wright.api` with `KgNode`/`KgEdge` | `uv run pytest tests/contracts/test_ingestion_hooks.py` + the existing span/boundary suites green | done |
| ING-2 | The docling-layout NLP segmenter as the default (eval first: a mixed legal + non-legal gold set; tiling + boundary quality) | `uv run pytest tests/ingestion` + `RAG_EVAL_TEXTILE_DIR=… uv run python -u eval/segmenter_eval.py` (gate PASS); live on real client PDF/XLSX + a public PDF | done |
| ING-3 | `Span.kind`; the generic structural unit grouper (`group_units`, 6,000-char cap) as the default; the reference pack's `provision_units` grouper (provision rules + legal furniture filter + Jev decider) sharing one grouping function with `clause_extraction_jobs` | `uv run pytest tests/ingestion tests/subgraphs/test_contract_ingestion_pipeline.py` + `RAG_EVAL_TEXTILE_DIR=… uv run python -u eval/unit_grouper_eval.py` (gate PASS) + parity 90/90 on 45 contracts | done |
| ING-3b | Move the reference pack's legal grouping/segmenting PATTERNS (section words, legal abbreviations, furniture labels) from Python into the contract pack `.ttl` as declarative data (ADR-0066), behaviour byte-identical | parity on the 45-contract set; `.ttl` drift check | todo |
| ING-4a | Spreadsheet content: hidden sheets INCLUDED by default (option to skip); compact (unpadded) table text for spreadsheet sources only (contract table text unchanged); database-style tables grouped ONE RECORD PER ROW (header as context), form grids stay one unit | unit tests + `eval/unit_grouper_eval.py` (record/block table labels on the local client workbooks) + contract parity | todo |
| ING-4b | The public `build_ingestion` builder in `rag_wright.api`: engine-owned wiring (chunk, layouts, segment, tag, index, group, extract, write, X/N progress, dead-letter, checks enforced, hidden-sheet count reported) with the ING-1 hooks | hermetic tests; LIVE non-legal ingest of the client workbooks/forms into ArcadeDB | todo |
| ING-4c | The contract pipeline re-expressed as the reference pack's instantiation of `build_ingestion` | byte-identical parity (spans + jobs) on the fixtures and the 45-contract set; LIVE contract re-ingest (zero regression) | todo |
| ING-6 | Embedded files as CHILD documents: files embedded in XLSX/DOCX (e.g. lab-report PDFs in a sheet cell) are extracted at parse, ingested as their own documents, and linked to their anchor cell/row in the parent (provenance) | unit tests + eval on the 64 embedded reports of the local client workbook | todo |
| ING-7 | Deterministic TABULAR record extractor: map a record table's column headers to the pack's properties via `.ttl` synonyms (ADR-0066); exact values, zero LLM calls; free-text columns left to an LLM extractor | eval on the local client database sheets | todo |
| ING-5 | Docs + skill: "Identifying your domain's units" + the stage-ownership table in `kg-construction.md`; new skill `building-an-ingestion-capability`; links from `authoring-capabilities.md`, the domain-adaptation README, `using-the-rag-wright-engine`, the product-starter templates, CLAUDE.md; G3/G4 resolved in the gaps register | doc link check; skill conformance | todo |
