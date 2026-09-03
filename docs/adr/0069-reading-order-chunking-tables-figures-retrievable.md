# ADR-0069: The chunker consumes the reading-order body (text + tables + figures), so tabular/figure content is retrievable — not only `document.texts`

Date: 2026-09-03
Status: Accepted (implemented; engine issue 0014, filed by RuleWright)

Builds on **ADR-0058** (structure-first chunking from docling labels) and **ADR-0025/0029** (operative-span
segmentation + citation offsets). Extends the PROD-3 / ADR-0050 no-silent-loss invariant from the failure
accounting into the chunk front-end.

## Context

The chunker and every structural pass in `rlm_chunking.py` (`_document_items`, `StructuralBoundaryDiscoverer`,
`_join_span`) read **only `document.texts`**. A docling parse puts a table in `document.tables` and a figure in
`document.pictures` — **never** in `document.texts`. `document_to_text` (the text stages' input) is
`export_to_markdown()`, which **includes** tables — so a fee schedule appeared in `SourceDocument.text` but was
**never chunked, never segmented, never indexed, and never retrievable**.

The loss was **silent**: the table never reached any stage that records a failure, so a table-bearing contract
ingested with `status=ingested`, `clause_failures: 0`, `span_failures: 0`, `total_failures: 0` — and no question
about the table could be answered. RuleWright hit it on the first table-bearing document measured (issue 0014):
a born-digital PDF whose only copy of the Enterprise annual fee (48,000) was a table cell parsed cleanly, ingested
"losslessly", and returned `not_found` on every fee question. Reproduced live on the fixture: docling parsed
`texts=6, tables=1`; the table with 48,000 was in `.tables[0]`, absent from `.texts`; 0 of 3 chunks and 0 of 4
spans carried it. Fee schedules, payment tables, and liability caps in tabular form are ordinary in commercial
contracts, so this is not an edge case.

## Decision

Chunk the document's **reading-order body** (text items **and** tables **and** figures), keep a table **atomic**,
and make the projection a **strict superset** of `.texts` so nothing can vanish upstream of the failure
accounting.

1. **A single reading-order content view (`content_items`, in `document_parser.py`).** It walks
   `iterate_items()` across the BODY and FURNITURE layers, mapping each item to a duck-typed `ContentItem`
   (`.label`, `.level`, `.text`): a TABLE → its `export_to_markdown()` (caption-prefixed), a PICTURE → its
   caption + description-annotation text, any other item → its `.text`. The chunker reads a document only through
   `.texts`, so a thin `_ContentView` exposing `content_items(document)` as `.texts` routes every discoverer,
   `_document_items`, `_join_span`, and `_validate_partition` through tables/figures with **no other change**.
   Because iterate order is reading order, a table folds into its own section's chunk (the fee table lands in the
   "1. Fees" chunk, next to "…the schedule below"), so its offsets and citations stay coherent.

2. **A table is one atomic operative span (`segment.py`).** The segmenter detects a contiguous markdown-table
   block (≥2 pipe-led lines) and forces cuts only at its edges, suppressing every interior paragraph/sentence
   cut — so the whole table (header row + all data rows) is **one** retrieval unit. The header carries the
   column semantics (that 48,000 is the *Annual fee (GBP)* for *Enterprise*), which a row-per-span split would
   strip from every non-header row. Byte-faithful tiling is preserved.

3. **No silent loss (the issue's Q2).** `content_items` is a **strict superset** of `document.texts`: any
   `.texts` item the reading-order walk did not visit is appended (coverage backstop), and a table/figure with no
   extractable text yields an empty item the chunker strips exactly as it strips an empty text item today. A
   content unit can no longer disappear before the stage that would record a failure — the mechanism that made
   0014 silent.

Figures (PictureItem caption + VLM/description annotations) flow through the same view as first-class chunk
content, so PR-3's "images, figures and tables" is covered by one projection, not three code paths.

## Consequences

- **Issue 0014 fixed, live-verified.** Real ArcadeDB KG + real BGE rerank + a capable OpenRouter answer model on
  the fixture: the fee table is chunked into its section (5 spans, one atomic table span classified *Payment
  Terms*), reaches the served/rehydrated evidence pool, and "What is the annual fee for the Enterprise tier?"
  answers **"48,000 GBP"** with a citation; the liability-prose control still answers (no regression). The
  ingest reports 0 failures **because there is no longer a loss**, not because a loss is hidden.
- **Retrieval vs generation are separable.** The fix is a **retrieval-layer** guarantee (the table reaches the
  evidence). The GENERAL/granite answer substrate over-abstains even on a direct prose hit (a separate, known
  generation-model limit); the smoke asserts the deterministic retrieval-layer proof independently of it, and
  demonstrates the end-to-end answer with a capable model.
- **Domain-neutral / retargetable.** The view is built from docling's structural labels (table/picture/heading),
  which are domain-agnostic; no contract-specific code. Any docling-parsed corpus (contract, policy, regulation)
  gets table + figure retrievability.
- **The transient compliance segmentation path** (`chunk_texts`/`achunk_texts`, SEG-2/SEG-5) goes through the
  same view, so a compliance subject's tables are segmented too. A `.texts`-only stub (or a `_SubDocument`
  slice) that exposes no `iterate_items` falls back to its `.texts` — no reading-order body to recover, no
  tables, unchanged behavior.
- **New committed assets.** `tests/fixtures/table-bearing-contract.pdf` (a generic MSA, self-contained) and
  `scripts/table_retrieval_smoke.py` (the live regression guard). Hermetic regressions in
  `test_document_parser.py`, `test_segment.py`, `test_rlm_chunking.py`.
- **No identifier or schema change.** `chunk_id`/`span_id`/offsets are unchanged; a table simply becomes real
  chunk text (so `canonical_document_text` now includes the table markdown, as CU-B1 always intended — the
  reconstruction from chunks, not the raw parse).
