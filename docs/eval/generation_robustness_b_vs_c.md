# Generation robustness: B (reason→emit) vs C (best-of-N), 2026-08-08

Motivation: vLLM-Granite-8B answer generation abstains flakily near its boundary (the WHITESMOKE
"how is liability capped, and under what conditions?" query abstained ~2/3 of runs at temperature 0).
We compared two candidate fixes head-to-head before considering escalation (lever A).

- **Baseline** — the single structured `generate` call (current production).
- **B** `generate_answer_reasoned` — a free-text reasoning node, then a structured emit node that only formats.
- **C** `generate_answer_best_of_n` — K structured samples at temperature 0.7, keep the best-cited
  non-abstaining sample; abstain only if all K abstain.

## Method

`scripts/measure_generation_robustness.py`, on the self-hosted A100 (vLLM-Granite) + Modal KG. Evidence is
built ONCE per query (target function → serve clauses → attach ADR-0044 carve-outs → rehydrate span text) and
the SAME fixed evidence is fed to all three strategies (the LLM classifier is NOT in the loop, so we measure
only generation-strategy variance). 3 answerable queries × 4 repeats × 3 strategies; C fans out 3 samples;
concurrency 3 (higher concurrency inflates per-request latency past the 120s seam timeout into a retry
cascade — a finding in itself).

## Results

| strategy | abstain rate | flip queries | mean citations | latency/answer |
|---|---|---|---|---|
| baseline | **67%** (8/12) | **0/3** | 3.0 | ~27s |
| B (reason→emit) | 83% (10/12) | 1/3 | 5.0 | ~132s |
| C (best-of-3) | **50%** (6/12) | 3/3 | **7.0** | ~146s (spiked to 341s) |

Per-query abstains (out of 4 repeats):

| query | baseline | B | C |
|---|---|---|---|
| Cap On Liability | 4 | 2 | 3 |
| Governing Law | 0 | 4 | 1 |
| Termination For Convenience | 4 | 4 | 2 |

## Verdict

- **B loses outright.** Splitting reasoning from emission made Granite-8B *more* likely to abstain (83% vs
  67%) — the free-text reason node over-hedges and talks itself out of answers. It BROKE the easy query
  (Governing Law: baseline 4/4 answer → B 4/4 abstain), helped only Cap, and cost ~5× latency. Rejected.
- **C is the better of the two but not a clean win.** Lowest abstain rate (50%), richest citations (7.0), and
  it genuinely RECOVERED answerable content the single call never found — most clearly Termination, where
  baseline AND B abstained 4/4 but C answered 2/4 with 6–7 citations. But it flipped on all 3 queries (its
  temp-0.7 diversity even made the easy Governing Law abstain once), best-of-3 was too few (still 50% on the
  sticky queries), and it is ~5× slower with latency spiking under load.

## The load-bearing conclusion (→ lever A)

1. The baseline is NOT pervasively flaky at low concurrency (0 flips across 3 queries). The dramatic WHITESMOKE
   2/3 flip was a high-concurrency + specific-borderline-query effect, not the general case.
2. The remaining abstains are a CAPACITY problem, not a call-STRUCTURE problem: restructuring the call (B) made
   it worse; repeating-and-voting (C) recovers some answers but cannot do so reliably, because each individual
   sample is a weak 8B judgment on a borderline query. C proving Termination *is* answerable (6–7 cites) while
   baseline never sees it is the tell — the answer is reachable; the 8B just is not strong enough to find it
   reliably.

This points at **A — escalate the hard/borderline query to a stronger model** rather than more sampling or more
decomposition on the same 8B. C is a brute-force, expensive proxy for "get a better judgment"; a single
stronger-model call on the borderline cases is likely cheaper AND more consistent than best-of-5 on Granite.

Caveat: n=4 × 3 queries is directional, not definitive; the qualitative signals (B worse, C recovers-but-costly,
capacity-bound) are consistent and clear. B and C remain in the codebase (`answer_generator.py`, tested) as
building blocks — C's best-of-N in particular can serve as the escalation's fallback or be composed with A.
