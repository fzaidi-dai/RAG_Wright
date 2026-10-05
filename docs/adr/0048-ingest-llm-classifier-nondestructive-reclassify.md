# ADR-0048: INGEST-LLM-CLASSIFIER — LLM clause classification (multi-label + confidence) as a non-destructive, upsert-style reclassify pass

> **Status: SHELVED (ADR-0114).** The LLM clause-function classifier is shelved behind a flag; the trained SetFit ensemble is the default (ADR-0114), with classifier-first Step-3a (ADR-0115/0116).


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
- Sub-decisions (resolved): `FunctionScore = {function, confidence: enum high|medium|low}`, LLM returns ranked
  (primary first), floor ≥ medium, cap ≤3. Extraction is **primary-function only** (edges tagged by function, so
  per-function extraction is a no-migration extension); delta re-extraction fires only on a **primary** change.
  Delta handling = two gated phases: Phase A classify + upsert labels + mark primary-flip clauses' properties
  `AMBIGUOUS` (mark-stale) + emit a delta report; Phase B re-extract the stale delta (gated, resumable).
- **Query-side is unaffected (additive design).** Keeping `Clause.function`/`Span.function` = the PRIMARY label
  means Leg B (reads `function` for display + properties for boost) and intra-doc (reads `function` for the
  PREC-1a `[auto-tag:]` frame) need NO code change — they benefit passively from better labels + properties.
  ADR-0047 de-risked this: neither leg gates on function anymore, so a label change can't reshuffle a retrieval
  pool. The optional ways the query legs could *exploit* the richer labels (confidence-modulated `[auto-tag:]`,
  multi-label weighting, exposing `functions` on the contracts) are deferred to task **QUERY-EXPLOIT-MULTILABEL**
  (tasks.md) — none are required for the pipelines to keep working.

## Addendum (step 2): full-corpus taxonomy-gap curation → 44 → 52 labels + a curated FOLD alias map

The Phase-A reclassify pass (DEBUG 3-way, ~840 chunks / 6,157 clauses on the unified `ragwright_cuad_full`
CUAD+ACORD KG) surfaced, alongside the in-taxonomy relabels, a large **OTHER (out-of-taxonomy)** bucket: 954
distinct clause types the LLM named because the 44-label taxonomy had no home for them (32% of categorized
clauses fell to OTHER, 25% to NONE). This is the taxonomy-gap signal ADR-0048 option 2 designed the
`other_label` channel to capture. Left unaddressed, these clauses stay unroutable (OTHER/NONE) and their
properties unextracted.

**Curation (LLM-assisted, human-overseen).** `scripts/curate_taxonomy_gaps.py` takes the 135 recurring gap terms
(count ≥ 3) and, in **one global DeepSeek call** (via `build_structured`, ADR-0045 server-side; a first 45-per-batch
run was discarded because independent batches contradicted each other — the same concept got FOLD/DROP/ADD in
different batches), sorts each into **FOLD** (a synonym of an existing label), **DROP** (a structural artifact), or
**ADD** (a genuinely new clause function). The output is a *proposal only* (`data/eval/taxonomy_gaps/curation_proposal.json`,
gitignored) — nothing is applied automatically.

**Human reconciliation (the oversight step).** The LLM proposal was reviewed against the real 44 labels and
**3 mis-folds were rejected** (`Confidentiality→Non-Disparagement`, `Representations and Warranties→Warranty
Duration`, `Royalty Grant→License Grant`), the **royalty family was consolidated** into the new `Royalties` label,
and two wrongly-DROPped reals were **folded** (`Right of First Refusal→Rofr/Rofo/Rofn`, `Milestone Payment→Payment
Terms`). The approved delta is a **disciplined 8-ADD** (not the LLM's raw 32), plus the reconciled folds.

### Decision (step 2)
1. **ADD 8 labels** — `TaxonomyGapFunction` enum in `contracts/function.py`: `Confidentiality`, `Royalties`,
   `Payment Terms`, `Dispute Resolution`, `Record Retention`, `Security Interest`, `Condition Precedent`,
   `Force Majeure`. Genuinely distinct from CUAD/ACORD's 44 (CUAD has no generic class for any of these). Order
   stays stable (CUAD → ACORD-ext → gap) so a classifier retrain's label↔id map is reproducible. Taxonomy 44 → 52.
2. **A curated FOLD alias map** — `_FUNCTION_ALIASES` (55 aliases → canonical label), authored **in code** (no
   external map to drift, consistent with the `symbolic_validation` maps). `canonical_function` resolves an exact
   cased match first, then an alias; so recurring synonyms (`Limitation of Liability`→`Cap On Liability`,
   `Assignment`→`Anti-Assignment`, …) now route instead of falling to OTHER.
3. **DROP terms need no code** — they stay off-taxonomy (`canonical_function → None`), same as before.
4. **`symbolic_validation` coverage** — the ADR-0040 function→dimension map must cover the taxonomy exactly. The 8
   new functions' property-dimension profiles are **not yet characterized** (Phase B property re-extraction, which
   would populate real assertions to review, is deferred). Per this module's standing rule — *coverage is expanded
   deliberately, never by guessing a closed set we are unsure of* — they are added to an explicit
   `PERMISSIVE_FUNCTIONS` set (unvalidated, declared not silently absent) rather than modeled speculatively (which
   would risk false downgrades). The coverage invariant becomes **modeled XOR explicitly-permissive == taxonomy**.

### Consequences (step 2)
- Fewer unroutable clauses: the FOLD map + 8 ADDs reclaim a large share of the OTHER bucket at query and ingest.
- **No re-extraction yet.** This changes only the *label space and canonicalization*. The actual reclassification
  write (Phase A) and delta property re-extraction (Phase B) over the live KG remain the deferred next steps; when
  Phase B runs and yields real assertions on the 8 new functions, each moves from `PERMISSIVE_FUNCTIONS` into
  `FUNCTION_APPLICABLE_DIMS` with its observed dimensions.
- Identifiers unchanged; the additions are purely additive to `FUNCTION_LABELS`; strict contracts hold
  (`FunctionScore` still rejects any non-canonical label).
- The curation is reproducible from a committed script; the proposal/report artifacts stay gitignored under
  `data/eval/taxonomy_gaps/`.

## Addendum (Phase A executed): the reclassify WRITE over the live KG — classifier fix, never-null policy, audit+revert

Phase A (read clause text from the KG → batched-LLM classify into the 52-label space + folds → UPSERT
function/functions → mark primary-flips stale → delta report) was executed over `ragwright_cuad_full` (45,404
clauses / 6,320 chunks). Three findings reshaped it from the original design:

1. **The classifier had to change.** A dry-run showed the batched **granite-4.1-8b** classifier INVENTED free-form
   function names (44% of spans: "Exclusive Source of Supply", "Forecasting Obligation", …) that fell to NONE,
   plus omitted spans and was non-deterministic. Fixes: (a) the structured `function` field now advertises the
   closed 52+OTHER **enum** (`json_schema_extra`) so guided decoding hard-constrains the model (kept a `str` so a
   stray never crashes a sub-batch and the `other_label` gap channel survives); (b) switched the reclassify model
   to **Gemma-4-31b via OpenRouter, provider-pinned `coreweave/bf16` with fallbacks** (`OPENROUTER_PROVIDER` +
   `OPENROUTER_ALLOW_FALLBACKS`, the existing seam). Enum + Gemma eliminated invention (44%→0). A 2-pass agreement
   probe showed the LABEL is ~99.5% stable run-to-run; the instability is confined to the "has a function at all"
   (→NONE) decision.

2. **Never-null-on-NONE (user-approved).** ~40% of clauses "flip to NONE", but that signal is noisy AND often a
   granularity artifact (the original ingestion propagated a section's function onto every header/fragment span).
   So a flip-to-NONE is a **no-op** — it never overwrites the existing label. Only a REAL new label is upserted
   (with the flipped clause's property edges marked AMBIGUOUS via `store.mark_span_properties_ambiguous`); an
   unchanged primary still gets the additive multi-label `functions`. The write is thus strictly non-destructive:
   a clause can only move to another real label, never be nulled. Verified at scale: `function='NONE'` count = 0,
   45,404 clauses intact.

3. **Independent audit + selective revert (user-approved path).** 99.5% *stability* ≠ *correctness*: at aggregate
   scale Gemma over-attracts to a few new labels (esp. Payment Terms). `scripts/audit_reclass_flips.py` had an
   INDEPENDENT judge (**DeepSeek V4 Pro**, not the Gemma classifier) rule OLD-vs-NEW on the actual clause text for
   every transition with count≥30 (12 samples each). Result: 38/41 transitions KEEP (~6,190 clauses validated),
   3 REVERT. `scripts/revert_reclass_flips.py` restored the 277 clauses in the 3 failing transitions
   (`Liquidated Damages→Payment Terms` [50/50 tie], `Competitive Restriction Exception→Exclusivity` [17/83],
   `Covenant Not To Sue→IP Ownership Assignment` [17/75]) to their OLD labels (property edges left AMBIGUOUS).

### Outcome
- **28,413 clauses** relabeled/enriched; **~10,278 audited real→real label corrections** stand; **16,197**
  flip-to-NONE kept untouched; **15,322** property edges staled (the Phase B queue); 8 new taxonomy-gap labels
  populated. Full GCS backup taken pre-write; the write checkpoint holds old→new per clause (any transition is
  precisely reversible). Residuals: ~4,088 flips in small transitions (count<30) unaudited/left-as-is; 7 marginal
  KEEPs (NEW=58% on n=12) left as written.

### Consequences / next
- **Phase B (LLM).** Re-extract typed properties for the ~10,278 reclassified clauses whose edges are now
  AMBIGUOUS — conditioned on the CORRECTED function (which dimensions to pull), gated to the flipped delta (not
  the corpus). This is the granite property-extraction work; it turns corrected labels into corrected property
  graphs. Reverted clauses' AMBIGUOUS edges are resolved here too.
- Query legs read `function` (the primary), so the corrected labels are already live for retrieval; the multi-label
  `functions` field is additive (QUERY-EXPLOIT-MULTILABEL, still optional).
