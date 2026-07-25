# ADR-0029: The (b) retrieval pipeline is domain-portable; coupling is isolated to trained artifacts + two schemas

Status: accepted (2026-07-26)
Relates to: ADR-0025 (retrieval pivot: function-classify -> pool -> rerank), ADR-0026 (property schema +
extended function taxonomy), ADR-0028 (grounding judge / cascade); the adopted reranker operating point
"improved (b)" (memory `reranker-operating-point-b`, tasks.md).

## Context

We finalized the query-time reranker as "improved (b)": a two-stage retrieval pipeline ending in ONE Gemma
listwise call. Before building further on it we needed to know how tightly it is bound to CUAD/ACORD, whether
it generalizes across legal contracts, and what it would take to move it to a different document domain (e.g.
financial reports), assuming a reasonable eval set is obtained by some means.

The pipeline end to end:
- **Ingestion (once):** parse (docling) -> segment into operative spans -> **function classifier** (LegalBERT,
  45-class = CUAD-41 + 3) -> BGE-M3 dense+sparse embed -> **KG structural-feature extraction** (Gemma into the
  `RelationalClauseV2` schema).
- **Query:** decompose -> discriminator (Gemma) -> **function filter** (union-top-2) -> **first-stage cross-
  encoder (a)** (LegalBERT, fine-tuned on ACORD grades with hard negatives) -> **one Gemma listwise call**
  (per-candidate scoring + features-in-prompt, K=25) -> return top-N with provenance.

## Decision

Record, as a standing architectural characterization, that **all logic, prompts, model architecture, training
harness, and infrastructure in this pipeline are domain-agnostic. 100% of the domain coupling lives in (1) two
trained artifacts and (2) two schemas** -- never in code paths. Concretely:

**Domain-agnostic (reuse anywhere, zero change):** BGE-M3 embeddings; ArcadeDB store; the decompose prompt
("rewrite the query as the decisive test a clause must pass"); the listwise-rerank prompt logic ("score each
candidate against the test"); the cross-encoder architecture; the Modal training harness
(`scripts/distill/train_modal.py`); the eval harness; the LLM (Gemma/Pro) itself.

**Legal-domain-coupled (not CUAD/ACORD-specific, but legal):** the span segmenter ("operative span" is a legal
notion); the **KG feature schema** `RelationalClauseV2` (mutuality, favorability, carve-outs, indemnity-bearer
-- liability/indemnity/warranty structure); the LegalBERT base weights.

**CUAD/ACORD-specific (the trained artifacts + tuned config):** the function classifier's 45-class taxonomy and
weights (the taxonomy IS CUAD-41+3); the first-stage cross-encoder weights (fine-tuned on ACORD grades); the
discriminators and the best listwise config (K=25, scoring, features), which were tuned/measured on the 57
ACORD queries.

**Generalization within legal:** strong across the commercial-contract core (CUAD-41 covers most commercial
clause categories; the liability/indemnity/warranty schema and generic prompts apply broadly). Specialized
sub-domains (M&A, employment, real-estate, regulatory, IP-litigation) need taxonomy + schema extension.

**Adaptation recipe for a NEW domain (given an eval set of (query, chunk, grade) triples):**
1. **Reuse verbatim (~0 effort):** embeddings, store, decompose prompt, listwise prompt logic, LLM, the Modal
   training harness, and the eval harness (repoint at the new grades). The pipeline SHAPE transfers unchanged.
2. **Retrain two models (mechanism reused, only data + base swapped):** (a) the function classifier -- define
   the domain's section/function taxonomy, bootstrap labels with an LLM or from existing schemas (e.g. SEC
   item numbers / XBRL for financial), retrain with a domain base (e.g. FinBERT); a cheaper interim is
   dense-retrieval or LLM zero-shot routing with no training. (b) the first-stage cross-encoder -- fine-tune on
   the new graded pairs with hard-negative mining via the same harness, OR, when grades are sparse, **distill
   from the LLM teacher** (the P3 path) so no human grading beyond the eval set is needed.
3. **Redesign two schemas (domain-knowledge task, small):** the KG feature schema (demand-derive the domain's
   discriminative structural fields from the eval queries -- e.g. metric/period/segment/direction for
   financials; the extraction MECHANISM is reused), and the segmentation unit (report section / paragraph /
   table row instead of operative span).
4. **Re-tune the config (low):** re-run the `listwise_variants.py` sweep (K, scoring, features) on the new eval
   to re-find the best (b) config; the legal winners may not carry over verbatim.

## Consequences

- **Adaptation cost is bounded and mostly mechanical:** two model retrains on the harness we already have plus
  two schema redesigns; everything else is reuse. The LLM-driven parts (decompose, rerank, feature extraction)
  port for free because the LLM supplies the domain expertise.
- **The distillation path (P3) is the enabler for low-supervision domains:** with only an eval set (few graded
  pairs), an LLM teacher labels the domain's candidate pairs and trains the cheap first-stage CE -- no large
  human-graded training set required. This is the main reason to keep the distillation machinery even though it
  did not beat gold on ACORD.
- **The two real lifts are domain-knowledge, not engineering:** the type taxonomy and the structural feature
  schema. They cannot be fully automated, though LLM label-bootstrapping and demand-derivation-from-queries
  (as done for legal) reduce the manual effort.
- **Caveat / non-goal:** this ADR characterizes portability and records the recipe; it does not port the
  pipeline. The ACORD-tuned numbers (nDCG@10 0.702/0.634, the ladder in memory `reranker-operating-point-b`)
  are legal-domain measurements and do not transfer; each new domain must be re-measured on its own eval.

## References

- Pipeline artifacts: `src/rag_wright/spans/` (segmenter, classifiers, `RelationalClauseV2`), first-stage CE +
  listwise variants under `scripts/distill/`, store `src/rag_wright/store/arcadedb.py`.
- Memory `reranker-operating-point-b` (the adopted (b), cost/quality ladder), `topk-ordering-levers-t58b`.
- Related decisions: ADR-0025 (retrieval architecture this generalizes), ADR-0026 (function taxonomy + property
  schema), ADR-0028 (LLM-teacher / grounding machinery reused for distillation).
