# RuleWright handoff: engine issue 0032 resolved — a cited span now carries its source page(s)

Date: 2026-09-10 · **Re:** engine-issue 0032 · on `origin/main` (commit `63e6897`) · ADR-0095 (realizes CU-B5) · **New fields on the citation, no breaking change. Re-ingest needed to populate them on existing KGs.**

---

## TL;DR

The parse-time page provenance that was being computed and thrown away now survives to the span. Every cited span (`HighlightSpan`) carries:

- **`pages: list[int]`** — the 1-based source page(s) the span overlaps. **A list**, because a clause can cross a page boundary; you get `[7, 8]` for one that spans 7→8.
- **`page: int | None`** — the first page (`== pages[0]`), the singular convenience field.
- **`bbox: (l, t, r, b) | None`** — best-effort: present when the span maps cleanly to one parsed item that had a box; `None` otherwise (a multi-item/cross-page span has no single box).

`SpanRecord` carries the same (`pages`, `page`, `bbox`), and they're persisted on the `Span` vertex and returned by `highlight_serve`.

This is exactly the honest fallback you described: with the page you can say *"page 7 — exact location unavailable on a scanned page"* instead of landing on page 1 with no explanation. Where a bbox exists (born-digital), you get the exact highlight box too.

## What you must do: re-ingest to populate it

Page provenance is computed **at ingest** from the parsed docling document, so a KG ingested before this change has **empty `pages`** until re-processed. There is no edge-only backfill for this one — the pages come from the parse, not from anything already in the store. Re-ingest the documents you want click-through on. (Fresh ingests get it automatically.)

## Where it works, and one requirement on your ingest

Pages populate only when the parse carried provenance — i.e. when the document was ingested from **real bytes** through `parsed_source_document(...)` (which keeps the real `DoclingDocument` with per-item `prov` on `doc.parsed`). That's the path you already use for real PDFs, so you're set. A plain-text `SourceDocument` (no `.parsed`) has no page provenance and `pages` stays empty — correct and graceful, not an error.

**Please confirm on a real scanned PDF**: ingest one through your normal byte path, then check a returned span's `pages`. Docling's OCR provenance is what feeds this, and the scanned case (PR-3) is the one that matters most for you — we validated the store/serve round-trip and the mapping logic, but the docling-OCR-prov → pages path on a genuine scanned document is worth your eyes.

## One honest limitation

Cross-page pages are exact at **item** granularity — the common case (a clause is several parsed items and the page break falls between them → you get `[7, 8]`). The residual: docling sometimes emits a single paragraph as separate wrapped lines, which the engine merges (DEFRAG-1) and tags with the paragraph's **first** page. So a span sitting entirely in that paragraph's page-boundary tail reports the paragraph's start page, not the tail page. It's an under-report of one page on a wrapped paragraph that straddles a boundary — honest, never wrong about the clause being *near* that page. If that granularity bites, we can track per-line page ranges through the merge; tell us.

## Not changed

- No new retrieval capability, no API removed. `pages`/`bbox` are additive fields (default empty/None), so existing consumers are unaffected.
- The mapping is fully deterministic (no LLM call).

Reference: ADR-0095, `spans/page_map.py`, `corpus/document_parser.py` (`ContentItem`, `_prov_page_bbox`), `subgraphs/contract_ingestion_pipeline.py` (`_attach_page_provenance`), `spans/segment.py` (`to_span_record`), `contracts/span.py` + `contracts/highlight.py`, `store/arcadedb.py` (`Span.pages`/`bbox`, `upsert_span`, `spans_by_contract`), `capabilities/highlight_serve.py`.
