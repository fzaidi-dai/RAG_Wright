# T58b stage 2: the top-10 ordering gap — diagnosis + lever results (2026-07-25)

Follow-on to `2026-07-25-t58b-full-pipeline-rerank.md`. That run left a gap: condensed **recall@10 = 0.677
vs recall@20 = 0.900** — the reranker finds the gold but doesn't always float it into the first 10. This
records the diagnosis of that gap and the head-to-head of the levers we tried to close it. **Decision: we
optimize the *condensed* number (practical downstream-retrieval quality), not full-corpus leaderboard parity.**

## Clean harness

The full 17,715-grade run wasted work grading un-judged clauses it then discards; the condensed metric only
needs the **judged** clauses. `eval/condensed_pipeline.py` grades only `judged n pool` per query (~1,740
grades, ~5 min at 6/s), and **persists the per-query discriminator** so pointwise + every re-order variant
share the exact same test and re-runs are deterministic/cache-stable (the reproducibility gap from the first
run). Pointwise baseline here reproduces the full run: recall@10 0.674, recall@20 0.885, nDCG@10 0.706.

Two robustness fixes landed while building it: (1) **never cache a failed LLM call** — a transient Gemma
throttle had poisoned the cache with 0.0/`[]` fallbacks that a resume wouldn't retry; `_score` returns
`None` and `_listwise` returns `[]` on total failure, and neither is persisted. (2) fast-fail (25/40s
timeout, 0 client retries) + 3 local retries caps tail latency.

## Diagnosis (`scratchpad/topk_diagnostic.py`, condensed space)

Of **584 graded-gold in-pool**, 403 (69%) are in the condensed top-10. The **181 misses** split:

| Loss type | Count | Meaning | Fixable by |
|---|---|---|---|
| **genuine misrank** | **121 (67%)** | >=10 judged clauses strictly outscore the gold | a sharper/stronger signal — NOT a tiebreaker |
| **tie-loss** | **60 (33%)** | gold pushed past 10 only by equal-score clauses | any tiebreaker |
| of misses, in rank 11–20 | 150 (83%) | just below the cut | a top-20 re-order |

- **Heavy ties**: median **8 distinct score values per pool** — the "continuous" pointwise score still
  clusters, so large tie-groups form and the clause-id tiebreak decides ~60 gold positions arbitrarily.
- **Contrastive/within-family queries are the weak spot**, and their extra loss is *misrank*, not ties:
  hit@10 61.6% (contrastive) vs 75.9% (plain); misrank 28.5% vs 13.5%.

## Lever results (condensed, Gemma 4 31b, 57 queries)

| Ranking | recall@10 | recall@20 | nDCG@10 | Δrecall@10 | cost |
|---|---|---|---|---|---|
| **pointwise** (operating point) | 0.674 | 0.885 | 0.706 | — | — |
| + full listwise re-order | 0.659 | 0.885 | 0.692 | −0.015 | 57 calls |
| **+ listwise tie-break** | **0.692** | 0.885 | **0.715** | **+0.018** | 57 calls |
| + property-graph tie-break | 0.673 | 0.888 | 0.704 | −0.001 | free |

1. **Full listwise re-order HURTS.** Letting the model re-order all 20 adds variance that scrambles
   orderings pointwise already had right; the regressions (carveout-to-cap −0.30, mutual cap −0.26,
   IP-exception-to-waiver −0.26) outweigh the gains. Rejected.
2. **Listwise as a bounded tie-break helps a little** (+0.018 recall@10) — restrict the model's judgment to
   *only reorder equal-score clauses*, never touching confident orderings. The only positive lever; costs one
   serve-time call. Recovers part of the 60 tie-losses.
3. **Deterministic property tie-break is neutral** (−0.001), and *free*, but too sparse to help: query
   decomposition yields **~1 property constraint/query** (15/51 extract zero), **73% of judged clauses have
   zero query-matching properties**, and property match varies within only **30% of tie-groups**. Honest
   closure on the property leg for ranking — it may still serve downstream structured filtering/QnA facets.

**The 121 genuine misranks (2/3 of the loss) are unmoved by any re-ordering of the same model's own
judgments** — genuine model difficulty on compound-contrastive queries. Re-ranking tricks are exhausted.

## Practical verdict / operating point

- **Adopt pointwise (+ optional listwise-tiebreak) as the retrieval operating point.** Condensed
  **recall@20 = 0.885** on a hard attorney-graded benchmark with correct routing (function ceiling 0.94/0.99)
  is a strong feed-k for a downstream QnA/synthesis step. Un-judged crowding means production likely surfaces
  *more* true matches than the labels credit.
- **Cheap ranking levers are marginal** (best +1.8 pts recall@10). No free lunch left in reshuffling Gemma's
  own scores.
- **The real remaining lever is model strength on the ~10 hard compound-contrastive queries**
  (Pro-escalation-for-hard-queries, like the extraction cascade), a measured cost/benefit decision — NOT
  another re-order. Open, not yet run.

## Artifacts

- `eval/condensed_pipeline.py` — clean condensed harness: pointwise -> {full listwise, listwise-tiebreak,
  property-tiebreak}, persisted discriminators, crash-safe (no-cache-on-failure), X/N progress, model-swap.
- `eval/listwise_rerank.py` — standalone listwise stage (reused by the harness).
- Caches (gitignored, `data/models/`): `condensed_disc.json`, `condensed_scores.jsonl`,
  `condensed_orders.jsonl`, `condensed_qconstraints.json`.

Related: ADR-0025 (pivot), ADR-0027/0028; T58a ceiling; memory `retrieval-design-t58`,
`reachability-not-rankability`, `acord-eval-design`.
