# RuleWright: moving to rag-wright 0.13

What 0.13.0 changes for RuleWright (released 2026-10-15: PyPI and GitHub release `0.13.0`, PR #15). Do the 0.12
guide first. Nothing here is edited from the engine repo, and nothing breaks an existing call: four items add calls
and one changes how blank PDF pages are parsed.

## 1. Audit your parse cache for invented blank-page text (PS-46, PS-47) — do this one

**What changed.** Before 0.13.0 a blank PDF page (no ink, no text layer) was escalated to the `VISION_OCR` model,
which invents a page when asked to transcribe an empty one, and the parse kept that text (TexWright, 2026-10-15).
Now the scan-quality gate returns `ScanQuality.BLANK` for such a page: never escalated, never in
`ocr_unreadable_pages`, listed in `TieredOCRReport.blank_pages`.

**Why RuleWright is likely affected.** You ingest with an OpenRouter key set (`VISION_OCR` is configured), a blank
separator page in a born-digital contract has no text layer, and invented English text passes the post-escalation
check. The parse cache is keyed on content, so the fix does not redo an old parse.

**What to do.** Run `find_blank_pages_with_text(cache_dir, sources, progress=...)` from `rag_wright.api`:
`cache_dir` is your parse-cache root (searched recursively), `sources` your contract PDFs as paths or
`(name, bytes)` (a top-level source's bytes are not in the cache; embedded files are found there). For each finding,
delete its `cache_files` and re-ingest that contract. Tell us how many documents and pages it found.

## 2. New calls, optional

- `migrate_span_fields(config, *, corpus=...)` (PS-35): the pre-0.2.0 `Span` field migration through the public
  API, replacing the engine's `scripts/migrate_span_fields.py`.
- `Range(gt=, ge=, lt=, le=)` (PS-44): a `where` value for comparisons evaluated in the store, on every KG call that
  takes `where`; `kg_read` now accepts `NOT_NULL` too.
- `span_search(ws, query, ...)` / `aspan_search` returning `SpanHit` (PS-45): the engine's hybrid span search with
  the dense floor (ADR-0104), citation-ready, no reranking.

## Verify

Your fast and db suites green on `>=0.13.0`; bump the floor. No harness name changed.
