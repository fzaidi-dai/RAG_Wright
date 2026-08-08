# OpenRouter provider routing on the silver set (Gemma-4-31B, tag-parse), 2026-08-08

Finalizing the OpenRouter dev/eval provider config for the client-side tag-parse generation path (ADR-0045,
`answer_model_for` -> `TaggedFreeTextAnswerModel`). Same silver harness (`scripts/measure_silver.py`), same
model (`google/gemma-4-31b-it`), 3 repeats over the 13-record set. The only variable is OpenRouter provider
routing (`OPENROUTER_PROVIDER` / `OPENROUTER_ALLOW_FALLBACKS`, plumbed through `models/seam.py::_provider_pin`,
commit `5c91daf`) and worker concurrency.

## Result

| routing config | RECALL (answered/ans) | PRECISION (abstain/neg) | flips | latency **median** | avg | errors |
|---|---|---|---|---|---|---|
| `deepinfra/turbo, Cerebras, friendli` + fallbacks (8 workers) | 27/27 (**100%**) | 6/12 (50%) | **0/13** | 45.6s | 41.8s | 0 |
| `Cerebras, deepinfra/turbo, friendli` + fallbacks (8 workers) | 25/27 (93%) | 6/12 (50%) | 2/13 | 38.4s | 32.6s | 0 |
| **`Cerebras` pinned, no fallback, 2 workers** | 27/27 (**100%**) | 6/12 (50%) | **0/13** | **1.74s** | 11.1s | 0 |

## Read

**Cerebras pinned, no fallback, low concurrency is the winner** on everything that matters: 100% recall, 0
flips (fully deterministic), median **1.74s** — ~26x faster median than either fallback fleet, with the
single-provider determinism intact. Most calls land at 1-7s.

**Cross-provider fallback is a bad trade for a reproducible product.** Under 8-way concurrency Cerebras
429-throttles and `allow_fallbacks` silently shunts the bulk of long-output generations to `deepinfra/turbo`
(~35-60s), so ordering barely helps latency; worse, heterogeneous engines give different completions and
**reintroduce non-determinism** (Cerebras-first dropped to 93% recall / 2 flips). The fallbacks did prevent hard
errors (0 in every run) — they just bought reliability by trading away both speed and determinism.

**The remaining blemish is the rate ceiling, not the mechanism.** In the pinned run, avg (11.1s) >> median
(1.74s) because 6/39 calls spiked to ~61s: Cerebras 429 backoffs that the client retried (exponential backoff)
and Cerebras itself then served — hence 0 errors, no fallback triggered. Even at 2 workers, ~15% of calls touch
the limit. Any *shared* provider has this ceiling; owning the engine is the only way to remove it.

**Backend-portable.** This OpenRouter/Cerebras run's 100% recall / 0 flips / ~1.7s median matches the
self-hosted 31B-W4A16 100% recall (`silver_selfhosted_gemma4_26b.md`) — the tag-parse path behaves identically
across serving backends, so the eval measures the model+path, not the provider.

**Precision (50%) is provider-independent** — identical across all three runs. The two misses (`Insurance`,
`Minimum Commitment`) are near-miss over-answering (topically-adjacent evidence that doesn't contain the clause;
both are honest "evidence doesn't specify..." prose that should have emitted `<abstain/>`, not fabrication) — an
abstention-discipline / model-strength lever, tracked separately from provider choice.

## Decisions

- **Dev/eval via OpenRouter:** pin **Cerebras single, no fallback, low concurrency (2-3 workers)** — the new
  `measure_silver.py` default. Do NOT use cross-provider fallback for eval (it destroys determinism + speed).
- **Production:** self-hosted Gemma on Modal/A100 stands (ADR-0039) — single engine = determinism, no rate
  ceiling, no 429 spikes. This run confirms the reasoning: a shared provider will always hit its limit.
