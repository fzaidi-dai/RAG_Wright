# ADR-0032: NL->type via two-step reason->emit on the GENERAL model (Gemma)

> **Status: SUPERSEDED (ADR-0045).** The two-step reason→emit guided-decoding path is replaced by client-side tag-parse (ADR-0045).


Status: accepted
Date: 2026-07-26
Related: ADR-0006 (model profile / Qwen thinking-disable), ADR-0023 (DeepSeek default, Gemma as benchmarked
exception), ADR-0031 (Gemma for structured chunking), CLAUDE.md standing rule (reason+structured -> split),
CU-C1, CU-D2.

## Context

`understand_query` (CU-C1, the NL->type front door) originally used STRUCTURED_REASONING (DeepSeek V4 Pro) as
a **default, not a benchmarked choice**. Pro is throttled (the CU-D2 eval took 409s for 208 predictions) and
costly. The question: can the cheap GENERAL model (Gemma) do NL->type instead?

A direct single-call `build_structured(gemma, QueryIntent-loose-schema)` **returned None on every call** --
Gemma, like Qwen (ADR-0006), rejects/fails a forced structured call when its reasoning mode is on for a richer
schema (nullable fields). The sanctioned remedy (CLAUDE.md standing rule; ADR-0006) is to **split reasoning
from the structured emit**, and to keep provider flags in the profile, not node code.

## Decision

1. **`understand_query` is a two-step reason->emit capability.** Step 1 reasons in free text (a GENERAL-model
   strength -- no forced tool, so no thinking-mode tool rejection) and ends with explicit `TYPES:/INTENT:/
   VALUE:` lines; step 2 emits the strict schema from that reasoning. Boundary normalization is unchanged
   (canonicalize labels, derive `in_taxonomy` from what maps); a failed emit (None) degrades to out-of-taxonomy
   low-confidence, never a hard error.
2. **Default model = GENERAL (Gemma).** NL->type joins chunking (ADR-0031) and OKF enrichment (ADR-0023) as a
   **benchmarked** Gemma exception to the DeepSeek-default rule (ADR-0023) -- not an unbenchmarked default.
3. **Gemma's profile gets the structured thinking-disable** `{"reasoning": {"enabled": false}}` (structured-only,
   like the Qwen secondary; free-text/reasoning calls -- including the step-1 reason node -- are untouched).
   Verified this does NOT break Gemma's simpler structured paths: chunking `_BoundaryList` still yields valid
   partitions and `_Summary` still returns a summary.

## Benchmark (CU-D2, same LLM-generated + spot-checked eval set, 176 in-tax / 24 out-of-tax / 8 multi-type)

| config | type acc (any) | type acc (exact) | multi-type | out-of-tax | 208-pred latency |
|---|---|---|---|---|---|
| Pro, single-step (old default) | 0.881 | 0.619 | 1.000 | 0.833 | 409s |
| Pro, two-step | 0.807 | 0.722 | 0.500 | 1.000 | 389s |
| **Gemma, two-step (adopted)** | **0.881** | **0.756** | **1.000** | 0.833 | **141s** |

Gemma two-step ties the best any-match, wins exact-match, keeps perfect multi-type, at ~3x less latency/cost
and no throttling. The two-step *degrades* Pro (any-match and multi-type drop) -- Pro reasons-and-emits fine in
one call -- so the split is Gemma's enabler, not a universal win; Pro remains selectable via `model_id`.

## Consequences

- NL->type runs on the cheap, un-throttled model with equal-or-better accuracy; the eval that exposed this
  (`eval/nl_to_type.py`) is the regression guard, results at `docs/eval/nl_to_type_cu-d2.md`.
- Out-of-taxonomy detection is 0.833 (vs Pro-two-step 1.000): a few concepts (Arbitration, Payment Terms,
  Confidentiality) leak to a semantically-adjacent taxonomy label instead of the fallback. Acceptable for the
  MVP; a lever if it matters is a Pro pass on low-confidence or a stricter out-of-taxonomy check.
- Many scored "misses" are CUAD label-adjacency (Agreement Date vs Effective Date; Competitive Restriction
  Exception vs Non-Compete), so real quality exceeds the 0.881 headline.
- The two-step is the standing pattern for any future reason+structured node on a GENERAL-class model.
