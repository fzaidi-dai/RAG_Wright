# ADR-0075: Per-page VLM escalation — VLM only the degraded pages, not the whole document

Date: 2026-09-04
Status: Accepted (implemented; PARSE-3, bulk-ingestion wall reported by RuleWright)

Completes the tiered-OCR hardening (ADR-0070/0073). Those fixes stopped born-digital pages from being
VLM-escalated; this one bounds the cost of escalating a *genuine* image-only page.

## Context

`TieredOCRParser` escalated a degraded scan by re-parsing the **whole document** through the VLM
(`vlm.convert(source)`). After ADR-0070/0073 the common false-positives are gone, but a residual landmine
remained: a large document containing even one genuinely image-only page (0-char text layer, correctly not
born-digital) still triggered a whole-document VLM re-parse. On a 60+ page contract that is ~30 min of VLM work
over OpenRouter → it blows the 600s parse deadline (`_INGEST_PARSE_DEADLINE_S`) → the document is dead-lettered.
The VLM only needs to read the one image page.

## Decision

Escalate only the degraded pages. `TieredOCRParser.convert` now calls `_escalate_degraded_pages`, which:

- `_page_count(source)` (pypdfium2) + `_escalation_runs(n_pages, degraded)` partition the pages into CONTIGUOUS
  runs, each tagged fast or VLM — e.g. a 63-page doc with page 31 degraded → `[(1,30,fast),(31,31,vlm),(32,63,fast)]`.
- each run is parsed with the right parser via `parse_range(source, (start, end))` (docling's native `page_range`,
  added to `DoclingParser` and `VlmOCRParser`), and the sub-documents are merged with
  `DoclingDocument.concatenate` (verified faithful: a two-range concat reconstructs the full parse exactly —
  same texts, tables, chars, and page numbers).
- VLM cost therefore scales with the number of DEGRADED pages, not the document length.

Fallback: when the page count is unknown or a parser has no `parse_range` (a non-PDF, or an injected stub),
`_escalate_degraded_pages` reverts to the whole-document `vlm.convert(source)` — preserving prior behavior.

## Consequences

- **Deadline landmine closed, live-verified.** A constructed mixed 6-page doc (pages 1–3, 5–6 born-digital;
  page 4 a genuine image-only page): `convert` escalated **only page 4** (`escalated=[4]`) in 14.4s and merged
  into a complete document. On a large doc the VLM now touches only the image pages, never all 60+.
- **Same PARTIAL semantics.** A page the VLM still cannot read is flagged `unreadable` (PARTIAL) exactly as
  before — never silently ingested.
- **Faithful merge.** `page_range` preserves page numbers and `concatenate` reconstructs the document, so
  downstream chunking/segmentation/citation offsets are unaffected.
- **No API/identifier/schema change.** New internal helpers plus a `parse_range` method on the two real parsers;
  the `Parser` seam gains an optional `parse_range` (fallback covers parsers without it).
- Closes the last parsing landmine from RuleWright's sample. Remaining: the doc-1 throughput observation (slower
  but 0-failure — more real extraction work after DEFRAG-1, not a failure) and the TAGPARSE-INGEST-1 backlog.
