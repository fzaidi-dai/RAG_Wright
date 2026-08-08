# Self-hosted Gemma-4-26B-INT8 on the silver set (free-text tag-parse), 2026-08-08

The end-to-end validation of a self-hosted single-model generator (product direction: one model on Modal, no
OpenRouter), using the client-side free-text + XML-tag-parse path that bypasses vLLM's broken guided decoding.
`scripts/measure_silver.py`, self-hosted `google/gemma-4-26B-A4B-it` INT8 on **A100-40GB** (the current card),
3 repeats over the 13-record silver set, `answer_model_for` -> `TaggedFreeTextAnswerModel`.

## Result

| model / serving | RECALL (answered/answerable) | PRECISION (abstained/unanswerable) | flips | latency |
|---|---|---|---|---|
| Granite-8B (self-hosted, today's prod) | 8/27 (**30%**) | 9/12 (75%*) | 2/13 | ~2-30s |
| Gemma-4-31B (OpenRouter) | 27/27 (100%) | 6/12 (50%) | 0 | ~120s |
| **Gemma-4-26B-INT8 (self-hosted, tag path)** | **24/27 (89%)** | 6/12 (50%) | **0/13** | ~46s |

Per-answerable-record (answered-count / 3): Cap 3, Uncapped 3, Governing 3, **Termination 0**, Non-Compete 3,
Audit 3, Anti-Assignment 3, Exclusivity 3, Revenue 3. Only Termination abstained (the 26B MoE is weaker than the
31B, which answered it on OpenRouter).

## Reading

- **The self-hosted path WORKS end-to-end.** Recall **89% vs Granite's 30%** — on your existing A100-40GB, no
  OpenRouter, no GPU upgrade. Zero flips (perfectly stable). The vLLM guided-decoding runaway is GONE: free-text
  terminates (~46s/call) and the client-side tag parser recovers answer + citations reliably.
- **Precision 50% is the same soft-negative story, better than it looks.** The two "misses" are the weak-evidence
  negatives: on Insurance the answer is a *defensible caveated* report ("The evidence provides the following
  information regarding insurance and guarantees..." + citations, marked AMBIGUOUS) -- not fabrication; on the
  redacted Minimum-Commitment it over-reads (as Granite AND OpenRouter-Gemma also did). On the two CLEAN
  off-topic negatives it abstained 3/3 -- **perfect**.
- **The 26B's only recall gap is Termination.** It's the smaller MoE (MMLU-Pro-class below the 31B).

## Conclusion + the corrected fallback

The product path is validated: **self-hosted Gemma-4 + free-text tag-parse** replaces Granite for generation and
lifts recall 30% -> 89% on the current hardware.

IMPORTANT correction to the earlier plan: the "26B fails -> A100-80GB with 31B" fallback is aimed at the wrong
axis. The 31B-W4A16 (~20GB) ALSO fits the **A100-40GB** (proven: it loaded + served earlier). The limiter was
never VRAM -- it was structured output, now solved by the tag path. So to close the remaining recall gap toward
the OpenRouter 31B's 100%, the next step is simply to run the SAME silver on the self-hosted **31B-W4A16** on the
SAME 40GB card via the SAME tag path -- no 80GB needed. Set `MODEL=google/gemma-4-31B-it-qat-w4a16-ct` (no QUANT,
util 0.80) in `scripts/modal_gemma4_vllm.py` and re-run.

Caveats: silver (not SME-vetted); vLLM 0.26; the strict binary precision label under-credits the caveated
Insurance answer.
