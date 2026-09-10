# ADR-0095: carry parse-time page provenance through to the span (CU-B5)

**Status:** accepted · **Date:** 2026-09-10 · **Issue:** engine 0032 (RuleWright) · **Realizes:** CU-B5 (the deferred page/bbox overlay named in the `Chunk` docstring) · **Related:** issue 0014/ADR-0069 (the reading-order content view), CU-A1/CU-B1/CU-B2 (doc-absolute offsets), `scan_quality` (already reads `prov[0].page_no`)

## Context

Docling records each parsed item's page (`prov[0].page_no`) and a best-effort bbox. `scan_quality` reads it, but the reading-order projection everything downstream consumes (`ContentItem`) kept only `label/level/text` — so page provenance was computed at parse and dropped before a span existed. `SpanRecord` had `page`/`bbox` fields (CU-A1) but they were never populated, and `page` was singular (a clause crossing a page boundary would truncate). RuleWright needs page-level click-through so a user can jump from a cited clause to its page in the retained original; without it, the feature falls back to text-matching the PDF's text layer, which fails silently on scanned PDFs (exactly where OCR was needed) — "a degraded feature vs a lying one".

## Decision

Thread page provenance from parse to span, deriving a span's pages from the document-absolute offsets it already has (per-span, offset-derived — cross-page correct).

- **`ContentItem` carries `page` + best-effort `bbox`** from `prov[0]` (`_prov_page_bbox`). The line-merge (`_merge_wrapped_lines`) keeps the merged paragraph's FIRST line's page and drops bbox (ambiguous once merged).
- **A page↔char-offset map** (`spans/page_map.py::build_page_offset_map`) reconstructs `[char_range) -> page` over the canonical document text by locating each item's stripped text in reading order with a forward cursor. Robust to the chunker's below-floor merges and heading folds (they regroup items but never change the item-text sequence); the only lossy case is an over-cap hard-split of a single item (rare), left unlocated.
- **`pages_for(map, doc_start, doc_end)`** returns the distinct pages a span overlaps (a LIST — a clause crossing a boundary reports both) plus a best-effort bbox (only when exactly one item overlaps and it has a box).
- **Wiring:** the pipeline's `segment_fn` builds the map once per document (`_attach_page_provenance`, from the parsed doc's `content_items` + `canonical_document_text(chunks)`) and stamps `pages`/`bbox` onto each `OperativeSpan`; `to_span_record` carries them to `SpanRecord` (`pages`, `page = pages[0]`, `bbox`). Best-effort: any failure, a parse with no provenance (the text-only ingest leg), or a chunk with no offset leaves `pages` empty — the honest "no page" fallback, never a broken ingest.
- **Persistence + serve:** the `Span` vertex gains `pages` (ARRAY_OF_INTEGERS) and `bbox` (a JSON `[l,t,r,b]` string); `upsert_span` writes them; `spans_by_contract`/`all_spans_by_contract` select them; `HighlightSpan`/`highlight_serve` decode and return `pages` + `page` + `bbox` on every cited span.

## Consequences

- **A cited span now carries its source page(s).** The product can land a click-through on the right page and, on a scanned PDF with no text layer, be honest ("page 7 — exact location unavailable") instead of silently landing on page 1. bbox enables an exact highlight where the parser produced one.
- **Available only where the parse has provenance.** RuleWright's real-PDF path (bytes → `parsed_source_document` → `doc.parsed`, a real `DoclingDocument`) carries prov, so pages populate. The engine's text-only CUAD dev leg (`_parsed_from_text`) has no prov → `pages` stays empty, gracefully.
- **Re-ingest to gain it.** Page provenance is computed at ingest, so existing KGs have empty `pages` until re-ingested (the parsed docling doc is what supplies it). No new retrieval capability was added — a fact the pipeline already computed simply stops being discarded.
- **Known limitation (documented):** a paragraph that docling emitted as separate wrapped lines is merged (DEFRAG-1) and keeps its first line's page, so a span entirely in that paragraph's page-boundary tail reports the paragraph's start page. Cross-page precision is exact at ITEM granularity (the common case: a clause = several items spanning a page break); sub-paragraph page splitting is the residual, refinable by tracking per-line page ranges through the merge if needed.
