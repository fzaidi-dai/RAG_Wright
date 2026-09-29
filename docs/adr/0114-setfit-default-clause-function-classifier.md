# ADR-0114: Trained SetFit ensemble is the default clause-function classifier (LLM shelved behind a flag)

**Status:** Accepted (2026-09-29)

## Context
Clause-function classification runs in the ingestion segment step, once per chunk, behind the
`ClauseFunctionClassifier` seam (`spans/clause_function_classifier.py`). The default was an LLM tag-classifier
(`LlmBatchClauseClassifier`, Qwen3.8-27b via the model seam) — one network round-trip per chunk. That per-unit LLM
latency multiplies across bulk ingestion (a 500-document corpus of long contracts) and is the dominant, cold-start-
and rate-limit-bound cost of the segment step. Function is a SOFT tag (ADR-0047): it augments KG search and is
never a gate, so its implementation can change with no downstream contract impact.

A converged offline experiment (`~/work/clause-classifier-ab/`) trained a SetFit soft-tagger (a Sentence-Transformer
body + a small classification head) as a drop-in for the LLM classifier. Final model: a 3-backbone ensemble
(LegalBERT + BGE-large + MPNet), each trained on capped-per-class all-available data plus curated silver for the
rare/tail classes. On the symmetric held-out eval it clears >0.65 per-class recall for 50/52 clause types at ~3
tags/span (avg-prob top-3). The recipe is captured in the `setfit` skill.

## Decision
Make the **trained SetFit ensemble the DEFAULT** clause-function classifier for ingestion; **shelf the LLM
classifier behind a flag** (`RAG_FUNCTION_CLASSIFIER=llm` reverts; default `setfit`). Implementation:
- `SetFitClauseAdapter` (new) implements the SAME `classify_spans(chunk_text, span_texts) -> list[list[FunctionScore]]`
  seam as `LlmBatchClauseClassifier` / `LegalBertClauseAdapter`. Span-level; emits avg-prob top-3 soft tags,
  continuous probability mapped to the existing `FunctionConfidence` enum INSIDE the adapter. **No contract / API /
  MCP / capability-registry change** — it is a new implementation of the already-registered
  `clause_function_classification` capability (CAP-REG-2).
- Runs **in-process, no `setfit` dependency** (Sentence-Transformer body + joblib sklearn head, needs only
  sentence-transformers + scikit-learn + joblib). Loads finalized checkpoints from `data/models/setfit_clause/`
  (`ckpt:cap128b_{legalbert,bge,mpnet}`), gitignored; env `RAG_SETFIT_CLAUSE_DIR` / `RAG_SETFIT_TOPK` /
  `RAG_SETFIT_THRESHOLD` / `RAG_SETFIT_DEVICE` tune it.
- **Dependency upgrade** required to load the models: `sentence-transformers` 5.6.0 -> 6.1.0, cascading
  `transformers` 4.57 -> 5.8.1 and `huggingface-hub` 0.36 -> 1.33. Full regression: 1586 tests pass, and BGE-M3
  embeddings are byte-identical pre/post (no retrieval drift).

## Consequences
- The segment-step classify goes from a per-chunk network LLM call (seconds/chunk, cold-start + rate-limit bound)
  to a local millisecond operation (~37 ms/span, CPU) — removing the multiplying latency + API cost from the
  ingestion hot loop. Exact end-to-end steady-state delta not pinned here (a warm on-contract flag-flip is the
  clean vehicle); the order-of-magnitude reduction is unambiguous.
- Coverage: 50/52 clause types clear the >0.65 per-class bar at ~3 tags/span. The 2 holdouts (Irrevocable/Perpetual,
  Affiliate License-Licensor) are license SUBTYPES the model tags as the parent "License Grant" — acceptable
  multi-tag for a soft tag; revisit with an external corpus only if a clean 52/52 is later required.
- The LLM path remains fully available (`RAG_FUNCTION_CLASSIFIER=llm`) and unchanged; no code deleted.
- Checkpoints live on the Modal `setfit-clause` volume (registry + snapshot mechanism) and are downloaded to the
  gitignored engine model cache for serving.
