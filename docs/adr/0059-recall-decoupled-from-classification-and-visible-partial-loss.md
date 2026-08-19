# ADR-0059: Retrieval recall is decoupled from classification; structure survives ingest; a partial loss is never silent

Status: Accepted (2026-08-19)
Date: 2026-08-19
Component: the intra-document QA subgraph (`subgraphs/intra_document_qa.py`), the clause-serving capability
(`capabilities/contract_kg_serve.py::contract_clause_index`), the operative-span segmenter (`spans/segment.py`),
the chunking capability (`capabilities/rlm_chunking.py` — the `BoundaryDiscoverer` seam + `_finalize_chunks`),
and the ingestion drivers (`subgraphs/contract_ingestion_pipeline.py`, `subgraphs/async_ingestion.py`). Raised
by: RuleWright (product), engine issue 0006.
Related: ADR-0058 (structure-first chunking — this extends it downstream), ADR-0047 (whole-index retrieval pool
— the recall principle applied here), ADR-0050 (PROD-3 lossless ingest — `partial` / `clause_failures`),
ADR-0025 (small-to-big span/clause indexing), ADR-0057 (async wall-clock deadline).

## Context

Engine issue 0006. RuleWright's probe (`intra_document_qa`, a 24-clause near-duplicate contract) returned
`not_found` on **clauses that demonstrably exist in the index** — 2 of 6 probe questions, and the tell was that
even *verbatim* query text missed. An engine-side full-ingest repro (the exact fixture → the real pipeline →
live ArcadeDB + local BGE-M3) pinned four independent root causes, not one:

- **D — classification-gated recall hole (primary).** `intra_document_qa` served clauses from the Clause KG
  (`clauses_in_contract`), which holds **only clauses the classifier typed**. The classifier returned `NONE`
  for all three "Term & Renewal" spans (33/52 spans `NONE`) → no Clause-KG node → the clause was absent from
  what QA could serve, though its body was fully indexed in the span store. A classification miss silently
  deleted a clause from retrieval. This contradicts ADR-0047 (retrieval ranks over the whole pool; a type is a
  boost, not a gate). The earlier belief that the classifier was "mostly unused" was wrong: it was load-bearing
  for Leg-A recall in exactly the wrong way.
- **B — segmentation left standalone headings.** `segment_clause` split at paragraph/sentence boundaries with a
  25-char sub-floor, so a bare section header (`9. Limitation of Liability`, 26 chars) became its own span,
  got typed as a clause pointing at a heading, and polluted evidence → intermittent abstain.
- **A — the chunk floor discarded structure.** `_finalize_chunks` applied a 1000-char (`MIN_CHUNK_CHARS`) prose
  floor to *every* discoverer's spans, so a short-clause document — where docling (ADR-0058) found N section
  headers — collapsed into ONE chunk, throwing structure away and forcing the classifier to read whole-document
  context per span.
- **C — a silent span-write swallow.** The retrieval-index write wrapped each `upsert_span` in
  `except Exception: continue`, dropping a span on any failure (e.g. a unique-index collision or a SQL-newline
  error) with no log or record — an invisible loss, contra NFR-2.

A fifth issue surfaced from RuleWright's retest of the fix (**ENG-1**): once C made span losses real, the
`IngestionReport.partial[]` entry carried them under an *optional* `span_failures` key, so an integrator reading
only `clause_failures` (the natural choice) saw a clean ingest — a silent trap. The async job path
(`async_ingestion.run_job`) was worse: it read only `clause_failures` and dropped span failures before they
ever reached the JobStore.

## Decision

Four fixes, one principle each, plus the reporting hardening:

1. **Recall is decoupled from classification (D).** `intra_document_qa` serves the **whole span index — typed
   AND untyped** — via `contract_clause_index(store, contract_id, include_untyped=True)`. An untyped span is
   appended as a bare `CitedClause` (`function="NONE"`), so a classifier `NONE` can never remove a clause from
   the candidate pool. The type remains a ranking/boost signal (ADR-0047), never a gate. Classification quality
   still matters (a good type improves ranking and downstream typed queries) but is no longer a recall
   precondition.

2. **A heading never stands alone (B).** `segment_clause` folds a **bare heading forward into its body**:
   `_is_bare_heading` (a short, Title-case, terminator-free line after stripping a leading `9.`/`(a)`/`12.1`
   enumeration via `_LEADING_ENUM`) is merged into the following provision by `_merge_subfloor`. The
   byte-faithful tiling invariant is preserved. `_is_bare_heading` becomes the single heading-text authority,
   reused by (3).

3. **Structure survives chunking (A).** The structural discoverers (`StructuralBoundaryDiscoverer`,
   `StructuralModelFallbackDiscoverer`) advertise `respects_structure = True`. When set, `_finalize_chunks`
   skips the prose floor (`_merge_below_floor`) and runs `_merge_bare_headings`: every complete docling section
   keeps its own chunk **however short**, and only a bare heading/title folds into a neighbour (reusing
   `_is_bare_heading`). The token cap still hard-splits; no text is lost. The prose/heuristic path (non-structural
   discoverers) is unchanged.

4. **A span-write failure is surfaced, never swallowed (C).** `_write_all` collects each failed `upsert_span` as
   `{span_id, reason}` and returns `{span_count, span_failures}`; `span_failures` threads through
   `IngestionState` and flags the document **PARTIAL** in the report and the `X/N` line, mirroring
   `clause_failures` (ADR-0050). The span index stays best-effort — a write failure never dead-letters the
   document — but the loss is always visible.

5. **The partial-loss signal is un-missable (ENG-1).** Every `partial[]` entry carries an always-present,
   kind-tagged `failures` list (`{"kind": "clause"|"span", "span_id", "reason"}`), built by one shared
   `build_partial_entry` helper used by **both** the blocking driver and the async job runner (which previously
   dropped span losses). The per-kind keys stay for back-compat. An integrator keys on `failures` (or on the
   document appearing in `partial`); the contract is documented on `IngestionReport.partial` and in
   `docs/product/engine_async_api.md`.

## Consequences

- **Verified end-to-end** on RuleWright's fixture (full re-ingest, then RuleWright's own retest with a fresh
  salt so caches could not fake it): Term & Renewal served 0 → 3 (RuleWright: 30/30 answered, zero `not_found`,
  abstains 5/5 → 0/5); no heading-only typed clauses; the document chunks per section instead of collapsing to
  1; ingest reports 0 clause and 0 span failures.
- **Bonus, empirically confirmed:** the per-section classifier context that (3) restored let the classifier
  correctly *type* the previously-`NONE` Term & Renewal clauses (`Renewal Term` / `Expiration Date`) and pick
  up a missed Indemnity (`NONE` 29 → 25) — a ranking/quality gain on top of the recall fix.
- **Cost, measured and accepted by the product:** (3)'s section-per-chunk granularity added ~70% wall-clock on
  large documents (15pp 28.6 → 48.9s) and pushed the 1-page case (9.2 → 12.1s) past RuleWright's NFR-1 ≤10s.
  RuleWright accepted the trade (a silent recall hole is a correctness failure; latency is a UX cost). If
  revisited, the lever is **batching the per-section summary/classify fan-out for small documents**, NOT coarser
  chunks — coarsening would hand back the recall/typing win that per-section context bought.
- **New behaviour integrators see:** more, smaller chunks on structured documents; more clauses typed; and a
  span-write failure now yields a PARTIAL entry rather than a clean-looking ingest.
- **A `NONE` type is no longer a data-loss event** for retrieval — it degrades ranking, not recall. This makes
  the classifier a soft signal system-wide and lets classifier improvement be an eval-gated ranking question
  rather than a correctness emergency.
- **Open on this arc:** re-run the ACORD graded-recall gate (A + D change chunk granularity and the corpus-wide
  serve pool, so the grounded-bar numbers may move).
