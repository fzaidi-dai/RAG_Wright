# ADR-0023: A cheap model role for OKF signpost enrichment (Gemma-4-26b-a4b), this task only

> **Status: SUPERSEDED / RETIRED (ADR-0025, ADR-0046).** The FR-K embedding-free OKF navigation experiment was shelved by the retrieval pivot (ADR-0025) and retired when ACORD folded into one production KG (ADR-0046).


Date: 2026-07-22. Status: Accepted. Adds a fourth model-profile role, `OKF_ENRICHMENT`, defaulting to
`google/gemma-4-26b-a4b-it`, used only by the OKF bundle-compile enrichment step (FR-K.2, T46). Every other
call class stays on its existing DeepSeek/Gemma role. Extends the T11 model-profile seam (ADR-0006).

## Context

OKF signpost enrichment (T46) classifies each ingested clause into a corpus-appropriate category and writes a
one-line description (ADR-0022 decoupled this from `graph_extraction`'s CUAD ontology). This is a simple
classify-and-describe task, unlike the extraction/grading/synthesis calls the system runs on DeepSeek V4 Pro.
Over the ACORD corpus it is 3,931 calls per compile, so the model choice has real cost weight.

The standing rule (CLAUDE.md, ADR-0006) is that a model choice for a structured-output call class lives in the
model-profile seam keyed by role, in config and a dated ADR, never as a hardcoded flag at a call site.

## Decision

Benchmark, then bind through the seam. On 20 seeded-random clauses, `google/gemma-4-26b-a4b-it` vs
`deepseek/deepseek-v4-pro` for the enrichment schema (`{category, description}`):

- **Category agreement: 20/20 (100%)** between the two models.
- On the 4 gold clauses in the sample vs the qrels-induced label: **identical (3/4 each)**; the shared "miss"
  is a silver-label artifact (the clause is plainly the category both models chose), not a model error.
- **Descriptions:** Gemma's one-liners are accurate, concise, and discriminating; DeepSeek's are slightly
  richer but frequently exceed the one-line target. For an `index.md` signpost, Gemma's tighter output fits.
- **Latency:** Gemma median 1.24s vs DeepSeek 4.71s (~3.8x faster); Gemma is also far cheaper (MoE, 4B active).
- Gemma takes the forced tool call cleanly through the seam (default `function_calling`, no `extra_body`),
  with 0 structured-output errors on the bench.

So: add `ModelRole.OKF_ENRICHMENT` (env override `RAG_MODEL_OKF_ENRICHMENT`), default
`google/gemma-4-26b-a4b-it`, registered profile with the default method and no extra body. `SeamClassifier`
(okf/enrich.py) resolves the model via `model_for(ModelRole.OKF_ENRICHMENT)`, not a hardcoded id. **This role
is used only by OKF enrichment; nothing else in the system changes.**

## Consequences

- The 3,931-clause enrichment pass is ~4x cheaper and faster than it would be on DeepSeek V4 Pro, with no
  measured quality loss on the task that matters (category agreement 100%, gold accuracy identical).
- The full-corpus run confirmed the seam integration: 3,491/3,931 categorized into ACORD's 9 categories, 440
  honest `None of these` (recorded in `_uncategorized/`), and 1 clause needing the deterministic fallback after
  persistent structured-output misses. The cheap model occasionally returns no tool call (a `None` from
  `with_structured_output`), handled by an in-classifier retry, a gated cross-pass retry, and a deterministic
  first-sentence fallback so every clause still gets a bundle file (RAC-46). Not a reason to abandon the model:
  the miss rate is ~0.03% and the fallbacks are visible, not silent.
- Precedent: a task-specific role is the seam's intended extension point. If a future task wants a cheap
  classifier, it can reuse or mirror this role rather than hardcoding a slug.
- Bench script: `temp/okf_enrich_model_bench.py` (gitignored); reproducible at seed 20260722.
