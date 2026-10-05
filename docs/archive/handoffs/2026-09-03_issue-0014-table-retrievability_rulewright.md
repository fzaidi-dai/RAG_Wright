# Handoff to RuleWright — engine issue 0014 fixed: tabular (and figure) content is now retrievable (ADR-0069)

Date: 2026-09-03. From: RAG_Wright engine. Re: your issue 0014 ("tabular content ingests cleanly and is never
retrievable"). **Resolved** — the chunker now consumes the document's reading-order body (text + tables +
figures), not only `document.texts`. Live-verified on your fixture. Detail in `docs/adr/0069-*.md`.

## Root cause (confirmed on `table-bearing-contract.pdf`)

docling parses the page into `document.texts` **and** `document.tables` — a TABLE is in `.tables`, **never** in
`.texts`. `document_to_text` is `export_to_markdown()` (which *includes* tables → why your `.text` had 48,000),
but the **chunker read only `.texts`**, so the fee table was never chunked, never segmented, never indexed, never
retrievable. It reported `total_failures: 0` because the loss happened **upstream of every stage that records a
failure** — exactly the silent-success you flagged.

Answers to your three questions: (1) it was a **real gap**, now fixed — tabular content is expected to be
retrievable. (2) A dropped table should **not** report `total_failures: 0`; rather than add a new failure kind
for "table dropped", the fix removes the drop *and* makes the chunk projection a strict superset of `.texts`, so
a content unit can no longer vanish before the failure accounting. (3) The 24-clause doc answered because it is
pure prose (all in `.texts`); the table-bearing doc lost exactly its table. **Your markdown row-4 anomaly is
separate:** through the engine's own byte-parse entry point (`parsed_source_document` / `aparsed_source_document`)
a markdown-with-table parses *identically* to the PDF (`texts=6, tables=1`, the liability prose present), so it
is **not** reproducible in the engine. It is almost certainly your markdown `SourceDocument` bypassing docling
(`parsed=None`), which routes through the engine's text-only fallback (`_parsed_from_text`: one TEXT item per
line, no headings, one degenerate chunk). See the action item below.

## What changed in the engine you consume

1. **The chunker walks the reading-order body (behavior change, no API change).** `parsed_source_document` /
   `aparsed_source_document` are unchanged; the per-document ingest now folds each table into its section's chunk
   (adjacent to the prose that introduces it), segments it as **one atomic span** (header row + all data rows
   together), embeds and indexes it. Effect you'll see: **table questions answer with citations** where they
   returned `not_found` before. A whole table is one retrieval unit, so the column header (e.g. "Annual fee
   (GBP)") always rides with the data row — the generator can tell 48,000 is the Enterprise annual fee.

2. **Figures are covered too.** A PictureItem's caption and description/OCR annotation text flow through the same
   reading-order view as first-class chunk content — so PR-3's "images, figures and tables" is one path, not a
   table-only patch.

3. **No silent loss going forward.** The chunk content projection is a strict **superset** of `document.texts`
   (a coverage backstop appends any text item the reading-order walk missed), so a content unit can no longer
   disappear upstream of the failure list your accounting (ENG-1) reads.

## Actions on your side

- [ ] **Bump the engine version.** No breaking API / identifier / schema change (`chunk_id`, `span_id`, offsets
      unchanged; a table just becomes real chunk text).
- [ ] **Flip your strict `xfail` in `tests/test_scanned_and_table_pdfs.py`.** The table-retrievability assertion
      should now pass — a strict xfail will fail as an *unexpected pass*, re-verifying AC-3 deliberately, which is
      what you wanted.
- [ ] **Route markdown through docling (fixes your row-4 anomaly).** Build your markdown `SourceDocument` via
      `parsed_source_document(name="*.md", data=...)` (which carries `.parsed`) rather than
      `SourceDocument(text=md, parsed=None)`. The `parsed=None` path is the text-only fallback that flattens
      structure to one chunk; that, not the table, is why your markdown liability question stopped answering.
- [ ] **Retrieval is fixed; generation is separate.** Your GENERAL/granite answer substrate over-abstains even
      on a direct prose hit in our runs (a known generation-model limit); a table now *reaches* the evidence
      deterministically. If your table questions still abstain with a weak model, that is the answer model, not
      retrievability — verify with a stronger model or inspect the served evidence
      (`contract_clause_index(..., include_untyped=True)` + `rehydrate_clause_texts` both carry the table span).
