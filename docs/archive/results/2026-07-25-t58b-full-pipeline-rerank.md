# T58b full-pipeline rerank — (a) condensed + (b) full-pool nDCG@10 (2026-07-25)

The end-to-end pivot ranker measured over all 57 ACORD test queries with **Gemma 4 31b throughout** (both
the query decomposer and the LLM reranker). This is the "does the pivot rank well" number, distinct from the
function-gate reachability ceiling (T58a) and the property-graph population checkpoint (T58, 2026-07-24).

## Pipeline

Per query: **oracle function** (best-single, the T58a-measured 0.939/0.992 ceiling) selects the pool ->
**Gemma decompose** (hardened prompt) rewrites the query into the *decisive test* a clause must pass (the
discriminator) -> **Gemma rerank** scores every pooled clause on a **continuous 0.00-1.00** relevance scale
against that test -> sort by score. Script: `eval/full_pipeline_rerank.py`. Reranker/decomposer are
env-swappable (`RERANK_MODEL` / `DECOMP_MODEL`). Crash-safe per-grade cache (`fullpipe_scores.jsonl`,
resumable), fast-fail (25s timeout, 0 client retries) so one hung call can't wedge the batch, X/N flushed
progress. Run: **17,715 grades, ~54 min, 5.4/s, 0 empty pools** (all 57 queries route to a real function).

## Results

| Metric | Value | What it is |
|---|---|---|
| **(a) condensed nDCG@10** | **0.702** | judged-only universe — the honest internal ranking signal |
| (a) condensed recall@10 | 0.677 | 68% of graded gold in top-10 |
| (a) condensed recall@20 | **0.900** | 90% of graded gold in top-20 |
| **(b) full-pool nDCG@10** | **0.179** | ACORD protocol, un-judged = 0 gain — **confounded, see below** |
| full recall@50 (ref) | 0.548 | confounded; vs bar 0.667 / two-leg baseline 0.379 |

Published full-corpus nDCG@10 baselines (context only, NOT apples-to-apples): BM25 0.540, MiniLM 0.572,
OpenAI-L 0.641, GPT4o-rerank 0.812.

## Why (a) and (b) diverge: ACORD pooling is too sparse *inside one function*

ACORD judges ~1,342 / 3,931 clauses per query. Restricting to one function pool (~266 for Uncapped
Liability) leaves ~55 judged and ~211 **un-judged** clauses, many of which are genuine matches ACORD never
rated. The reranker scores those high (correctly) and they fill the top-10, each contributing **0** gain ->
(b) collapses to 0.179. A full-corpus ranker (the published baselines) dilutes the same un-judged matches
across 3,931 clauses and is not penalized the same way. So **(b) is a floor, not a fair comparison**, and a
function-gated pipeline structurally cannot emit a fair full-corpus nDCG@10.

**(a) condensed** removes the un-judged clauses before scoring — it measures ranking quality over exactly
what ACORD graded. It is the metric we optimize. It is *not* comparable to the full-corpus baselines either
(easier universe), which is fine: we care about downstream retrieval quality, not leaderboard parity.

## Reading the condensed number for a real application

- **recall@20 = 0.900**: feeding a downstream QnA / workflow the top-20 of a function pool surfaces 90% of
  the graded-gold clauses. That is a strong recall base for a synthesis or QnA step.
- **recall@10 = 0.677 vs recall@20 = 0.900**: the gap is a *top-of-list ordering* problem, not a recall
  failure — the reranker finds the gold but doesn't always float it above near-miss same-family distractors
  in the first 10. This is the lever to work next (item 2 below).
- **Easy queries** (unique function, condensed nDCG@10 ~0.89-0.93, recall@20 = 1.0): Minimum Commitment,
  multiple governing laws, Third Party Beneficiary, Revenue/Profit Sharing, IP Ownership Assignment, ROFR —
  clean type tests. **Hard queries** are the within-family discriminations (seller- vs buyer-favorable cap,
  IP-infringement exception to a waiver, first- vs third-party indemnity) where the discriminator must split
  clauses of the *same* function.

## Process notes / caveats

- **Oracle function** is used to pick the pool (the ceiling is separately measured at 0.939/0.992). This
  isolates reranker quality; a live run would route via the decomposer's function head (T58b real path).
- The score cache is keyed by discriminator **text**, and Gemma's decompose is not byte-stable even at
  temp 0 -> offline re-analysis cannot cheaply reconstruct per-query results (a re-decompose misses the
  cache). Next run should persist `disc_of` + `query_id` alongside scores for reproducible per-query
  breakdowns.
- Continuous 0.00-1.00 scoring (vs the earlier coarse 0-3) is what lets the reranker break ties by degree;
  it improved condensed nDCG@10 materially in the single-pool grounding runs.

## Reproduce

```
uv run python -m eval.full_pipeline_rerank                 # all 57, Gemma throughout
RERANK_MODEL=deepseek/deepseek-v4-pro uv run python -m eval.full_pipeline_rerank   # swap reranker
LIMIT=3 FRESH=1 uv run python -m eval.full_pipeline_rerank # dry-run
```

Related: ADR-0025 (pivot), ADR-0027 (routing), ADR-0028 (judge+cascade); T58a ceiling; memory
`retrieval-design-t58`, `reachability-not-rankability`, `acord-eval-design`, `cuad-not-a-retrieval-benchmark`.
