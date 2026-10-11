# ADR-0133: The model-call deadline follows the configured timeout, and a thinking model's structured output is capped by its profile

**Status:** accepted · **Date:** 2026-10-11 · **Related:** ADR-0056/0057 (one bounded retry layer; the async
wall-clock deadline), ADR-0110 (the timeout trap on reasoning-on calls), PS-22

## Context

The 2026-10-11 sampling A/B (ContractNLI, 150 pairs, thinking on, Qwen on our A100) found two things:

1. The async seam cancelled every logical call at a fixed 180 s (`_MODEL_DEADLINE_S`), whatever
   `RAG_STRUCTURED_TIMEOUT_S` said. The docs and the qwen skill tell people to raise that per-try timeout for
   reasoning-on work, and past 180 s in total it did nothing. A loop and a slow healthy call both ended as
   `ModelCallTimeout`, and the ContractNLI eval scored that as "neutral".
2. Reasoning loops are real and random: one pair ran to 8,192 tokens in 1 of 3 tries at Qwen's recommended
   sampling, and correct verdicts took up to ~7k tokens. Qwen's recommended sampling did not remove loops (greedy
   stays the default).

## Decision

1. **`model_deadline_s()`** is `RAG_MODEL_DEADLINE_S` when set; otherwise the per-try timeout times the tries plus
   the largest backoffs, never under 180 s. Every async seam call runs under it, and `ModelCallTimeout` names both
   settings.
2. **`ModelProfile.structured_max_tokens`** is the default `max_tokens` for a forced structured call (a caller's own
   value wins). The four thinking-on Qwen profiles set 8,192, so a loop ends at `finish_reason=length`, a parse
   failure the bounded retry handles, rather than at the deadline. A server deployed from the engine inherits it.
3. **The cap never fails a call that worked without it.** vLLM rejects a request whose prompt plus `max_tokens`
   exceeds the context with a 400 naming `input_tokens` (`vllm/renderers/params.py`), so that error is answered by
   sending the call once more without the cap. A caller's own `max_tokens` keeps today's behaviour (no retry).
4. `eval/contractnli_judge.py` reports failed calls as errors, never as a verdict.

Rejected: vLLM's `thinking_token_budget` (needs `--reasoning-config` on the server and is not supported by vLLM's V2
model runner yet, and it is vLLM-only), and higher sampling temperature as a loop fix (measured, above).

## Consequences

- Raising `RAG_STRUCTURED_TIMEOUT_S` now raises the total budget too; the default is ~182 s (was 180 s).
- A looping thinking call costs at most 8,192 output tokens per try instead of running until cancelled.
- Free-text calls are not capped by the profile (thinking is off on that path in every Qwen profile).
- RuleWright: callers that turn a failed call into a result should treat `ModelCallTimeout` as an error, not a
  verdict (`rulewright-migration-0.7.0.md`).
