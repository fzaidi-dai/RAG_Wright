# ADR-0101: untagged spans reach clause extraction; the aspect gate is removed

**Status:** accepted · **Date:** 2026-09-12 · **Prompted by:** RuleWright (engine issue 0036) · **Related:** ADR-0082 (function is never an ingest gate), ADR-0081 (thematic tag-parse groups), ADR-0047 (function gate not load-bearing), issue 0033 (carve-out recall)

## Context

Clause-function classification is a **soft tag** (~0.37 top-1; `function-classification-not-load-bearing`). ADR-0082 declawed the symbolic validator so function is *never* an ingest gate. But one place still gated on it: `contract_ingestion_pipeline.clauses_fn` required `canonical_function(function) is not None` **and** `is_extractable_span(op.text)` before a span reached the extractor. `NO_FUNCTION` ("NONE") canonicalizes to `None`, so **every untagged prose span was silently dropped from the typed layer** — a coin-flip tag set the *size* of the clause KG. RuleWright measured the same 11-page contract yielding ~31 vs ~114 clauses depending only on the classifier model. This contradicted ADR-0082 and was unintended.

Extraction itself is already function-independent (only the span text reaches the tag-parse extractor; `function` is used afterward only for the `record.function` tag, the FOLIO IRI, and `symbolic_validate`, which no-ops on an unmodeled function).

Separately, the tag-parse extractor (ADR-0081) shipped an **aspect gate** (`RAG_INGEST_CLAUSE_GATE`, off by default): a coarse recall-biased pass that picked which of the 7 thematic groups to run, to cut cost. It was off by default because granite under-selected; the intent was to enable it once a stronger model was the default.

## Decision

1. **`is_extractable_span` alone gates clause extraction.** The `canonical_function(...) is not None` half is removed; an untagged-but-extractable span is extracted with `function=NO_FUNCTION`, carried as the soft tag. The guard is extracted into a tested `clause_extraction_jobs(segments)` helper (the load-bearing logic is now visible and unit-tested).
2. **`is_extractable_span` is tightened** with two deterministic, high-precision furniture rules — table-of-contents dotted-leader-to-page-number lines, and notice-block contact-label lines (`Attention:`/`Fax:`/`Email:`/`Telephone:`, extending the existing anchored signature-label pattern). It stays recall-first: a notice *clause*, a telephone-support provision, and section decimals remain extractable. A false-keep is a cheap thin clause; a false-drop is a lost provision.
3. **The aspect gate is removed entirely** (not merely left off). A live A/B on the current default model (Qwen 3.8-27B via OpenRouter), 24 real clauses, gate-off (all groups = recall ceiling) vs gate-on:
   - gate-ON property recall **0.817** (174/213); **22/24** clauses lost ≥1 property; the loss concentrated in **`excepts` (carve-outs)** — 17 of 39 misses — plus exclusivity/temporal/claim-scope.
   The gate loses ~18% of properties even on a strong model, in exactly the field (carve-outs) issue 0033 was about. It never earned its keep on any model we run, so keeping it off-by-default is just dead config; it and `RAG_INGEST_CLAUSE_GATE` are deleted.

## Consequences

- **The typed layer reflects the document's provisions, not the classifier's coin-flip.** Untagged provisions now become `Clause` nodes with their properties; `contract_clause_index` already serves `function=NONE` clauses with properties, so they reach RuleWright's `contract_terms`. The `include_untyped` flag (issue 0006-D), which served such spans as *bare* citations, is now largely superseded (they arrive typed).
- **Ingest cost rises**: every `is_extractable_span` prose span is extracted (~7 group calls each), where before only tagged spans were. `is_extractable_span` is the cost governor (fewer spans); the remaining cost lever is **span batching** (two spans per group call), which preserves per-group focus and therefore recall — unlike the removed gate. Group pruning is off the table.
- **Extraction stays function-independent end to end**; no code path now gates on the soft function tag.
- Observed but out of scope: on Qwen, `Subject`/`ExceptionModel`/`DamageType` closed vocabularies drop many verbatim phrases to OTHER (the issue-0033 phenomenon at scale) — a candidate follow-up, independent of 0036.
