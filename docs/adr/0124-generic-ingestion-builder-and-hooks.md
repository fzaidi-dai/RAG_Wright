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

## ING-4b addendum (2026-10-07): the public builder, tuning, and the packaged eval

- **`build_ingestion(extractor, ...)` -> `pipeline.aingest(ws, sources, cache_dir=...)`** (in `rag_wright.api`): the
  engine owns parse (hidden sheets, embedded children), chunk, layout, segment, tag, span indexing, grouping,
  concurrent extraction, writing, a `Document` node per document, X/N progress (`[ingest] i/N`, per child `j/M`), and
  dead-lettering. Every hook output is checked (`check_tiling`/`check_units`/`check_extraction`); a failed unit is
  recorded and skipped, a failed document dead-lettered, the run goes on (`IngestionReport` / `DocumentReport`).
- **Embedded children** are ingested through the same pipeline; the store gains always-on generic types
  (approved schema addition): `Document` (doc_id UNIQUE, parent_doc_id, filename, media_type, sha256),
  `EmbeddedIn` (child -> parent, first-anchor position) and `AttachedTo` (child -> the record-row `Span`, with the
  ING-6 `confidence` / `basis` / `evidence`). Edges are written with the new idempotent `Store.kg_ensure_edges`
  (create-if-absent, else update props; `outV()`/`inV()` endpoint match, verified live), so a re-ingest never
  duplicates. Derived document ids include the file type (`Report_v3_xlsm`), so a PDF export and its spreadsheet
  original never collide.
- **`IngestionTuning`** (unit cap, fragment floor, `RecordTableRule`, `IdentifierRule`, extract/document
  concurrency) is settable on the builder or via `EngineConfig.options.ingest.tuning`; `IngestSource.table_mode`
  (`auto`/`record`/`block`) overrides the per-table decision for a source. `to_span_record` moved to
  `rag_wright.contracts.span` (re-exported from `spans.segment`) so the generic path never imports the legal module.
- **`evaluate_ingestion(sources, ...)`**: the packaged structural eval (parse + deterministic chunk + segment +
  group; no model, no store) a developer runs on their own samples, with optional table labels, returning
  `passed` / `failures`.
- Live (local client data, `eval/ingestion_live_smoke.py`): 9 files + 64 embedded reports -> 73/73 ingested, 0
  dead-lettered, 6,743 spans, 1,319 records, 64 `EmbeddedIn`, 83 `AttachedTo` with their evidence on the attached
  row; a second run leaves every count unchanged.
- Deferred to ING-4c: an optional entity-graph hook (the reference pack's party extraction + entity resolution).

## ING-6b addendum (2026-10-07): PDF attachments, and empty documents

- **Files attached to a PDF** (its embedded-files tree) are extracted like ING-6 embeds -- content-addressed,
  `<parent>.emb.<sha12>`, duplicates merged, unreadable or empty ones reported (pdfium returns an empty attachment
  as the 8-byte Flate encoding of nothing; that is treated as empty). An attachment has no position in the page
  content, so it carries no anchor: the child gets an `EmbeddedIn` edge to its parent document and no `AttachedTo`.
- **`pypdfium2` is now a declared dependency** (`>=5.11`; it was only transitive via docling while the engine
  already called it for link-evidence text) and is in the framework grounding index.
- **A document with no text** (a blank or unreadable cover page, a sheet holding only attachments) is ingested as
  an EMPTY document -- its `Document` node and children recorded -- instead of being dead-lettered by the chunker
  ("no chunks produced"), which had silently dropped every attachment of such a document.
- Live sweep: the 68 real PDFs (client + embedded reports) carry no attachments -- 0 found, 0 skipped, no errors.

## ING-7 addendum (2026-10-07): the generic table-rows primitive (scope reduced on review)

- **`table_rows(source_document)`** (`rag_wright.api`) returns every parsed table's data rows as exact cells
  (`TableRow`: `columns`, `values`, `cell(name)`, table ref, sheet or page, 0-based grid row), read from the parse's
  cell GRID -- so a table the chunker split across chunks still comes back whole, with its header; merged cells
  arrive filled; tables of a skipped hidden sheet are left out. Works for CSV, spreadsheets and PDF tables.
- Inside `build_ingestion`, a unit holding exactly ONE data row carries it (`Unit.table_row`), so a domain
  extractor gets exact cells without re-parsing markdown. The row-to-span mapping now covers every table (not only
  spreadsheets), using the same renderer the chunk text uses.
- **Not in the engine (decided on review):** what a column MEANS, record types and column-to-property mappings
  are the DOMAIN's own capabilities (e.g. TexWright's extractor + its `.ttl`), built in the product repo. No
  `eng:ColumnMapping` vocabulary is added until a real domain shows it is reusable.
- Real data (local client workbooks): 8,812 of 8,812 cells identical to an independent reader (openpyxl, merged
  ranges resolved); in the builder, 162 of 162 one-row units carry exactly their own row.

## ING-4c addendum (2026-10-07): the contract pipeline on the shared stages

- **One set of stage functions, two drivers.** `IngestionStages` (`rag_wright.ingestion.builder`) holds the stages:
  parse, chunk, segment (tiling check, whitespace-only spans dropped, page provenance, tagging), index, extract
  (group, unit checks, table rows, concurrent extract + `check_extraction`), and write. `build_ingestion` drives them
  directly; the reference pack's `contract_ingestion_pipeline` keeps its seven-node LangGraph (node names and state
  keys unchanged, because RuleWright reads them) and calls the same stages inside its nodes. The contract-specific parts are
  plain hooks in the reference pack: the legal segmenter, `function_span_tagger` (one batched classify call per
  chunk), the provision grouper, the clause extractor (cached by chunk id, function, and template version), and
  the clause KG writer (`clause_kg_graph`, idempotent via `already_written`).
- **Provenance rule relaxed (approved):** any node or edge carrying a `span_id` must cite a span of its unit; any
  `confidence` must be EXTRACTED, INFERRED, or AMBIGUOUS; a non-empty extraction must cite at least once. Nodes
  without a span are shared vocabulary (e.g. a property value), not unsupported claims.
- **Jev metered and cached.** The boundary decider's call records usage (`record_usage`), and `cached_decider`
  caches decisions by decision model + span texts, so a re-run makes no decision call.
- **VISION_OCR now defaults to the product LLM (Qwen3.8-27B).** Gemma-4 was the default only because the earlier
  product LLM (Granite) was text-only. Qwen accepts images, and serving one model on one A100 (ADR-0110: FP8 Qwen,
  high concurrency, large KV cache) is cheaper overall than serving two. OCR now resolves its endpoint, served id, and
  free-text flags (reasoning off, provider pin) through the profile, so a self-hosted Modal server works too.
  `_vlm_available` follows the profile, not an OpenRouter key. docling makes the OCR request itself, so the engine
  now meters one uncosted call per OCR'd page; before this, OCR spend never showed up in `measure_usage`.
- **Live parity (set A: 5 PDFs + 5 CUAD text contracts).** Rebuilt pipeline vs the pre-port reference, sharing
  caches: 8 of 10 contracts identical record by record across every reference-owned node and edge type
  (200 clauses, 958 spans, 128 property values, all `HAS_*`/`COVERS`/`EXCEPTS`/... edges), with **zero Qwen calls**
  (6 Jev calls, $0.0004). The other 2 differ only because **Jev is not deterministic** (measured: 5 of 181 residue
  answers changed across 3 identical calls) and the reference run predates the decision cache. Proven offline for
  both: segmentation and tagging are identical (the residue batch hits the cache key), and flipping only the
  differing Jev answers (1 for Aimmune, a page footer; 3 for ACCURAY, a date line and two table-of-contents
  fragments) reproduces the reference's provisions exactly. The decision cache now makes re-ingests repeatable.
- **Fix (found by the ING-4d live re-ingest).** The reference pack's clause-extraction cache was keyed by the
  provision id (document, index, content hash) without its position. A document that repeats a provision verbatim
  (Aimmune prints its press release twice) could therefore, once indices shift between runs, reuse the OTHER copy's
  cached record; the provenance check rightly rejected it, but the clause then vanished silently (still counted in
  `clause_records`, absent from `clause_failures`). Now `clause_cache_key` includes the anchor span (position), and
  `settle_clause_results` drops a stage-rejected unit from `clause_records` and reports it in `clause_failures`
  (PROD-3 shape). The key change invalidates existing clause caches once.

## ING-8c addendum (2026-10-07): the reference pack lives in `rag_wright.packs`

- **Decision.** The reference pack is two domain packs, `rag_wright.packs.contracts` and `rag_wright.packs.compliance`
  (compliance is built on contracts and registers it first). Module paths mirror the old subpackages; a pack's own
  Pydantic contracts live under `schemas`. Each pack keeps its ontology (`.ttl`), skills and data files next to its
  code, and has a `pack.py` (manifests, canonical slugs, `register()`). No re-export shims: the old paths are gone,
  consistent with the ING-8 "no transition defaults, no dual reads" rule; the product migrates from
  `docs/specs/ingestion-hooks/ing8-breaking-changes.md`.
- **Enforced.** Two import-linter contracts: nothing outside `rag_wright.packs` imports a pack (the generic engine is
  every other package), and `packs.contracts` never imports `packs.compliance`.
- **Verified.** Full suite green; a live Aimmune re-ingest is record-identical to ING-8b (131 clauses, 105 property
  values, 700 spans).

## ING-8d addendum (2026-10-07): generic option and span field names

- **Decision.** `IngestOptions` holds only engine knobs (`tuning`); a domain pack's knobs travel in
  `EngineOptions.packs[<pack name>]` (the contracts pack's `ContractIngestOptions`), so the engine never names a
  domain field. The engine span fields are `document_id`, `primary_tag`, `tags`; `parent_okf_path` left the engine.
  New names only: `SpanRecord`/`Span` forbid unknown fields, and `ensure_schema` refuses a database whose `Span` type
  still declares the old fields. Existing databases migrate once with `migrate_span_fields`
  (`scripts/migrate_span_fields.py`), in batches, idempotently.
- **Verified.** Full suite and the live store tests green; a live Aimmune re-ingest is record-identical to ING-8c, and
  migrating the ING-8c database makes it identical to the ING-8d one.
