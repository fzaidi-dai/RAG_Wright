# ADR-0124: A generic ingestion builder with domain hooks; the legal pipeline becomes the reference instantiation

- Status: Accepted (ING-1: contracts; ING-2..5 build on it)
- Date: 2026-10-07

## Context

The reference ingestion pipeline (`aproduction_document_ingest`) hardcodes three domain decisions inside its stage
functions: the segmenter (`segment_clause`: legal enumeration markers and abbreviations, fused with the contract
function classifier), the extraction UNIT (spans grouped into contract provisions by `Section/Article/Clause N`
headings, a contract-trained boundary decider, and a legal furniture filter), and the extractor. A new domain's
extractor therefore receives a contract provision as its unit, which is a domain leak. The only seam,
`abuild_document_ingest` (seven stage functions), is internal and forces a domain to rewrite indexing, provenance and
writing to change one decision (engine gaps G3/G4).

## Decision

1. **One public ingestion builder; the engine owns the mechanism.** Wiring, chunking, the span index, page
   provenance, ids, writing, `X/N` progress and dead-lettering are engine-owned. A domain overrides only the
   domain-shaped hooks, each with a domain-neutral default:
   `segmenter` (default: a docling-layout-based NLP segmenter), `span_tagger` (default: none), `unit_grouper`
   (default: layout-structural grouping, else the chunk), `boundary_decider` (default: none), `extractor`
   (required), `writer` (default: `kg_write`).
2. **The hook contracts live in `rag_wright.contracts.ingestion`, exported from `rag_wright.api`:** `LayoutItem`
   (an engine-owned closed set of layout kinds, so hooks never depend on docling's labels), `Span`, `TaggedSpan`,
   `Unit`, `UnitExtraction` (the extractor returns `KgNode`/`KgEdge` in the pack's schema), and the hook protocols.
3. **The engine enforces the contract on every hook output, default or override:** `check_tiling` (spans tile the
   chunk text byte-faithfully under `span_id = <chunk_id>#<index>`), `check_units` (known spans, each at most once,
   in order, indexed 0..k-1; dropping a span is allowed, it stays in the index), `check_extraction` (each record
   node cites a span of its unit and carries a `ConfidenceTag`, FR-S.4).
4. **The legal pipeline is the reference pack's instantiation** of the builder (`segment_clause`, provision
   grouping, the Jev decider, the function classifier, the clause extractor and its typed writer), proven
   byte-identical by a parity test. Its legal patterns move to the reference pack as data (ADR-0066).
5. **Hooks are passed as callables to the builder.** Registering a hook by name as a `function` capability is a
   possible later extension, not part of this decision.

## Consequences

- A new domain chooses its own span and unit and writes only its extractor (plus any hook it wants to swap); the
  default path needs no legal knowledge.
- `OperativeSpan` is now an alias of the generic `Span` (its OKF locator became optional), and `BoundaryDecider` is
  one shared contract, so the reference implementations satisfy the hooks by construction.
- The identifier schemes (`chunk_id`, `span_id`, `entity_id`) are unchanged.

## ING-2 addendum (2026-10-07): the default segmenter

- **`rag_wright.ingestion.segment_layout`** is the engine's default `Segmenter`: layout-driven (the parse's items,
  projected onto chunk offsets by `chunk_layouts`; `text_layout` for uncovered text and text-only sources) plus
  domain-neutral NLP sentence rules. A **table yields one span per row** (header + separator together) for precise
  citations; the unit grouper (ING-3) keeps a table whole for extraction. Headings join the following span (or the
  previous one when they end the chunk); a tiny fragment folds back (forward only when it opens the chunk); a list
  number never ends a sentence. No legal rules; the reference pack keeps `segment_clause`.
- **DEFRAG-1 (wrapped-line rejoin) runs only for line-oriented sources** (PDF, scanned image, or no recorded origin,
  i.e. the one-item-per-line text fallback), decided by `document.origin.mimetype`. Markdown/DOCX/HTML/spreadsheet
  items are already paragraphs or form fields and are no longer glued together. Contract parity verified
  byte-identical (5 PDFs + 5 CUAD text contracts: items, chunks, legal spans).
- **`.xlsm` parses as `.xlsx`** (same Office Open XML package; macros never run). Spreadsheet sources are the
  recommended path for spreadsheet-born forms: the Excel parse recovers every form grid as a table, where the PDF
  export loses some to loose cells.
- `_is_bare_heading` (a domain-neutral text rule the generic chunker uses) moved from the legal `spans.segment`
  into `corpus.document_parser`; `rag_wright.ingestion` and `contracts.ingestion` are now import-linter sources that
  may not import the legal segmenter.

## ING-3 addendum (2026-10-07): the default unit grouper

- **`Span.kind`** (optional, closed `SpanKind`): what a span starts with, set by the segmenter (`table_row` for a
  table row after the header; `heading` when the span opens with a heading). A span without it is plain text to a
  grouper. Additive; no identifier or store change.
- **`rag_wright.ingestion.group_units`** is the engine's default `UnitGrouper`: a heading starts a unit and owns its
  content; a table (with the heading above it) is one unit, ended by the first non-row span; page furniture and
  text-free spans are dropped; a chunk change ends a unit; an optional decider adjudicates heading-like plain lines
  (one batched call, degrade to no split). Units are capped at **6,000 chars** (`DEFAULT_MAX_UNIT_CHARS`, measured:
  ~90% of contract provisions are below it) and split at span boundaries; a split table's continuation units repeat
  the header row in their text.
- **The reference pack's grouper is `provision_units`** (the legal provision rules, the furniture filter, the Jev
  decider), sharing one grouping function with `clause_extraction_jobs` so they cannot drift; parity 90/90
  identical on 45 contracts with and without a decider. No cap on the reference grouper (behaviour unchanged).
- Moving the legal grouping patterns themselves into the contract `.ttl` (ADR-0066) is tracked as ING-3b.

## ING-4a addendum (2026-10-07): spreadsheet content

- **Hidden sheets are ingested by default.** A spreadsheet's hidden worksheets (docling's INVISIBLE layer) are moved
  into the body at parse; `parse_document(..., include_hidden_sheets=False)` skips them and lists them in
  `SourceDocument.skipped_hidden_sheets` (never silent). The choice is part of the parse cache key; a pre-ING-4a
  spreadsheet cache is normalized on load. Client evidence: a hidden sheet held the R&D trial log.
- **Spreadsheet tables render compact** (no column padding; was up to ~80% spaces, and pushed a 65-row sheet over
  the chunk cap). Non-spreadsheet table text is unchanged (contract parity 10/10 byte-identical).
- **Database-style tables group one record per row** in `group_units`: a real header (3+ named columns, an unnamed
  index column allowed, 80%+ distinct after a merged cell's repeat across adjacent columns counts once) and either a
  serial first column or 8+ columns. Each row unit carries the header in its text; the header span joins the first
  row. Forms, key-value tables and criteria matrices stay one unit. Eval: 414 hand-labelled tables (client forms,
  database workbook, 64 embedded lab reports, a public report) -- zero forms split, every wide record table split;
  small record tables kept whole are a diagnostic (harmless).
- Known limit: a record table larger than one chunk even when compact is split mid-table by the chunker; ING-7's
  tabular extractor reads records from the parsed table grid, not chunk text.

## ING-6 addendum (2026-10-07): embedded files become linked child documents

- **Extraction** (`rag_wright.corpus.embedded`, generic over xlsx/xlsm/docx/docm/pptx/pptm): every part under
  `*/embeddings/` is one object. OLE objects are unwrapped (`olefile`, added as a dependency): a Windows Packager
  object yields the file and its display name -- the sender's local path stored beside it is discarded -- and an
  OLE-wrapped PDF/Office file yields its `CONTENTS`/`Package` stream; embedded package parts are taken as-is. Each
  file carries its anchor (sheet + cell + row/col, paragraph, or slide). Identical files are one child with all
  anchors; unextractable objects are reported in `SourceDocument.embedded_skipped` (never silent).
- **Storage + identity**: content-addressed beside the parse cache (`<cache>/<parent>.<hash>.embedded/<sha12>.<ext>`),
  never the source folder; child id `<parent_id>.emb.<sha12>`; the original filename is metadata.
- **Record links** (`EmbeddedChild.links`, FR-S.4 confidence): the anchor row is VERIFIED (EXTRACTED) when one of its
  identifiers appears in the child (PDF text layer via pypdfium2, plus the filename); otherwise the child is placed
  by content on the rows, anywhere in the workbook, whose identifiers it mentions (one record -> INFERRED, several
  -> AMBIGUOUS); with no evidence, a child alone in its cell keeps a position-only INFERRED link and a STACKED one
  (several children in one cell) gets none -- it remains a child document with its anchor as provenance. An
  identifier is a whole token (4+ chars, contains a digit) rare in the tables (<= 3 rows) AND among the files
  (<= 3), so a standard number or a year is never evidence; substring matching was rejected (it matched inside
  phone and report numbers).
- Eval (local client workbook): 66 objects -> 64 children + 2 duplicates, 0 skipped; every row holding children has
  a verified link (31/31); every single-child cell verified (18/18); 10 placed by content, 22 unplaced (each names
  a sample absent from the workbook), 0 ambiguous.
