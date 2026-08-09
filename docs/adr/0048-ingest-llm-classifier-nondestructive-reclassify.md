# ADR-0048: INGEST-LLM-CLASSIFIER — LLM clause classification (multi-label + confidence) as a non-destructive, upsert-style reclassify pass

## Context

ADR-0047 retired the precomputed clause `function` as a *query* pre-filter. The label is now used only at
**ingestion**, where it is load-bearing: it (a) gates which spans become `Clause` nodes and (b) conditions granite
property extraction (`symbolic_validation` maps function → which dimensions to extract). Two problems remain with
how the label is produced:

1. **Accuracy.** It comes from LegalBERT (macro-F1 0.5885). We want the **graph-building LLM** (granite/DeepSeek
   via the seam) to classify — the same model that already reads the clause for extraction, so it is more
   accurate and context-aware.
2. **Granularity + shape.** Classification runs **per span** and returns **one label**. But a span loses clause
   context (the force-majeure→Cap mislabel), and a long clause can genuinely carry **multiple functions**. We
   want **full-clause, multi-label, confidence-scored** classification.

**Hard constraint (user):** this must be **non-destructive**. The existing KG (139,955 spans, 45,404 clauses,
72,746 typed property edges — a >1h granite build) must remain; we will not rebuild it. Only the *classification*
changes, applied as an **upsert**. Re-paying granite property extraction for unchanged clauses is unacceptable.

Feasibility (grounded): `write_clause_kg` is `UPDATE Clause SET function=…` (in-place upsert by `clause_id`);
property edges UPSERT by `span_id`; `spans_by_contract`/`span_texts`/`clauses_in_contract` let us read existing
clause text from the KG with **no re-parse / re-chunk / re-segment / re-embed**. So classification and property
extraction are separable, and a reclassify pass can run over the live KG.

## Decision

Build **INGEST-LLM-CLASSIFIER** as a **decoupled, idempotent, upsert-style reclassify capability**, with a
build-from-scratch mode retained. Three layers:

### 1. A new classifier seam + contract (replaces the span/single-label shape)
- Contract: `classify_clause(clause_text) -> list[FunctionScore]` where `FunctionScore = {function, confidence}`
  — **full-clause, multi-label, scored**. A confidence floor decides which functions are kept.
- Implementations behind the seam: `LlmClauseClassifier` (the graph-building model via the model-profile seam —
  the default going forward) and a `LegalBertClauseAdapter` (wraps the existing classifier for back-compat).
- Inject it into `production_document_ingest` as a `classify_fn` param (threaded through
  `run_corpus_ingestion`/`run_cuad_ingestion`), defaulting to the LLM classifier. (Today the classifier is
  *hardcoded* — this adds the seam query-time already had.)

### 2. Multi-function clause KG (non-destructive migration)
- A clause may relate to **multiple** functions, each with a confidence. Represent it as a per-clause
  `functions: [FunctionScore]` (primary = highest confidence), with each **typed property edge tagged by the
  function it was extracted for**, so a multi-function clause holds per-function property sets.
- Migration is additive: an existing single-function clause becomes a one-element list; nothing is dropped.
- Provenance (FR-S.4): record the classifier id + confidence on the function assignment.

### 3. Two modes — `scratch` and `upsert` (the non-destructive path)
- **`scratch` (RESET=1):** the existing pipeline, but classifying at the clause level with the LLM seam + writing
  multi-function clauses. For a fresh corpus / clean rebuild.
- **`upsert` (RECLASSIFY=1, the default over an existing KG):** a standalone pass that:
  1. **Reads existing clauses' text from the KG** (group spans by `parent_chunk_id` → clause text). No
     re-parse/chunk/segment/embed — those results are reused as-is.
  2. **LLM-classifies each clause** → `[FunctionScore]` (concurrent, async+semaphore, X/N progress + monitored;
     content-hash gated so a re-run is a no-op).
  3. **Upserts the function label(s)** onto the existing `Clause`/`Span` nodes (cheap `UPDATE SET function`), plus
     the multi-function assignment + provenance.
  4. **Delta-triggers property extraction ONLY for the changed subset:** a clause whose function set is unchanged
     keeps its existing properties (**zero granite**); a clause that **gained** a function extracts *only that
     function's* dimensions; a clause that **lost** a function has that function's stale property edges dropped
     (cheap delete) or flagged. Granite cost ∝ the mislabel/multi-label delta, **not** the 42k corpus.

## Consequences

- **Non-destructive + cheap.** The reclassify pass reuses all parse/chunk/segment/embed/property work. Its cost is
  ~one LLM classify call per clause (~5,610 clauses, concurrent — minutes) plus granite re-extraction only for the
  changed delta. No >1h rebuild.
- **Accuracy + context.** Full-clause classification by the graph-building LLM fixes the span-context-loss
  mislabels (force-majeure→Cap) and captures genuinely multi-function clauses.
- **This is the PREC-1b Option A, realized non-destructively.** It also gives a natural place to *measure* the
  reclassification delta (how many clauses the LLM relabels vs LegalBERT) — the quantify step we deferred.
- **Property fidelity improves** where the function changed (properties re-extracted for the *right* function),
  which feeds the Leg B property soft-boost (still used post-ADR-0047).
- **Scope discipline:** identifiers unchanged (`clause_id`/`entity_id`/`value_key`); the multi-function field is
  additive; the store DDL change (per-clause functions list + edge function tag) is an ask-first schema change,
  staged so the existing single-function reads keep working during migration.
- Open sub-decisions for the build task: the exact `FunctionScore` schema + confidence floor; whether delta
  re-extraction is immediate or deferred (mark-stale then a bounded pass); and the multi-function property-edge
  tagging vs. keeping one primary function for extraction.
