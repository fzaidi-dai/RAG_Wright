# Query-side model A/B — granite-4.1-8b vs DeepSeek V4 Pro vs Kimi-k3

**Date:** 2026-07-29 · **Status:** noted fact, for future reference (granite stays adopted; nothing changed).

## What this measures

The adopted Leg-B router (KG-5e) makes **two query-side LLM calls per query**, wrapped in one function
(`capabilities/query_function_classifier.py::route_query`):

1. **constraints** — typed `(dimension, value)` extraction via docling-graph + `clause_template`, and
2. **function** — a taxonomy-constrained classification (closed `FUNCTION_LABELS`, `json_schema`).

Both currently run on **`ibm-granite/granite-4.1-8b`** (8B, open, self-hostable). This A/B swaps *both* calls
to two higher-end models — **`deepseek/deepseek-v4-pro`** and **`moonshotai/kimi-k3`** — to record how much a
larger query-side model would buy. LegalBERT top-3 (local) and BGE ranking are unchanged. Eval: `eval/kg_primary.py`,
`VARIANT=v4`, ACORD 57 queries, grade≥2 floor, condensed metrics. Reproduce with
`QUERY_MODEL=<id> LLM_FUNCTION_MODEL=<id> MODE=llm_union|llm`.

## Results — adopted `llm_union` pipeline (LLM functions ∪ LegalBERT top-3)

| Query-side model | recall@10 | recall@20 | nDCG@10 | Δ recall@20 vs granite |
|---|---|---|---|---|
| **granite-4.1-8b** (adopted) | 0.490 | 0.713 | **0.553** | — |
| deepseek-v4-pro | **0.512** | **0.735** | 0.551 | **+0.022** |
| kimi-k3 | 0.514 | 0.731 | 0.553 | +0.018 |

## Results — `llm`-alone (query→function routing quality only, no LegalBERT union)

| Query-side model | recall@10 | recall@20 | nDCG@10 |
|---|---|---|---|
| granite-4.1-8b | 0.434 | 0.620 | 0.503 |
| deepseek-v4-pro | 0.441 | 0.628 | 0.494 |
| kimi-k3 | 0.459 | 0.652 | 0.512 |

## Constraint-extraction behavior (call 1)

| Model | total constraints (57 q) | avg/query | zero-constraint queries | structured-output method | extraction reliability |
|---|---|---|---|---|---|
| granite-4.1-8b | 99 | **1.74** | 11 | `json_schema` (needed; ADR-0034) | reliable |
| deepseek-v4-pro | 68 | 1.19 | 12 | `function_calling` (default) | reliable |
| kimi-k3 | 72 | 1.26 | 14 | `json_schema` (needed; ADR-0034) | **unreliable** — docling-graph raised "no models" on some queries; the eval now guards per-query extraction failures |

## Findings

1. **A higher-end query-side model buys almost nothing here.** On the adopted `llm_union` pipeline the gain is
   **+0.02 recall@10/@20 and ~0 nDCG@10** (nDCG is 0.553 / 0.551 / 0.553 — a dead heat). The ceiling for
   swapping in a much larger, costlier model is a marginal recall bump, not a step change.
2. **Function-routing quality is similar across all three** (`llm`-alone r@20 0.620 / 0.628 / 0.652). The
   taxonomy-constrained forced-choice is an easy task; model size barely moves it.
3. **Granite extracts *more* constraints, not fewer** (avg 1.74 vs 1.19 / 1.26). The frontier models are
   *terser*, not more thorough — so granite's small nDCG edge is plausibly from richer constraint coverage.
4. **Structured-output quirks are per-model and empirical** (the model-profile seam, ADR-0006/0034): granite
   and Kimi silently return `[]` under `function_calling` and need `json_schema`; DeepSeek Pro works under the
   default. **Kimi's docling-graph constraint extraction is unreliable** (raises "no models" on some queries) —
   a real robustness cost.

## Verdict

**Keep granite-4.1-8b.** It is within 0.02 recall and *identical* nDCG of both frontier models on the adopted
pipeline, extracts constraints more thoroughly, is open and self-hostable, and avoids Kimi's extraction
flakiness. The marginal recall gain does not justify the added cost, latency, and provider dependency of a
frontier query-side model. Recorded for future reference; revisit if the residual oracle gap (recall@20
0.713 → 0.843) becomes worth chasing, where lever (a) fine-tuning is the likelier win than a bigger query LLM.
