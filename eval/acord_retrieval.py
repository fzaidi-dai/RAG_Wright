"""ACORD (T33) retrieval bar: recall@k (binary, grade >= 2 GATE) + nDCG@10 (graded, DIAGNOSTIC).

The retrieval half of the joint golden-eval (the answer half — citation-recall over synthesis — is
GraphWright's). For each test query, an injected `retrieve` runs the retrieve -> rerank -> fusion legs and
returns a ranked list of corpus-ids; this module scores it:

  - recall@50 (binary, relevant = grade >= floor): the pass/fail GATE (recall@50 >= 0.70).
  - recall@10 (binary): reported.
  - nDCG@10 (graded, full 0-4 scale, exponential gain 2^rel - 1 per BEIR/pytrec_eval): reported ranking
    diagnostic — deliberately NOT thresholded (nDCG rewards higher grades by design).

`retrieve` returns fusion output (corpus-ids), so chunk_read is not invoked here (the retrieval bar is
measured before rehydration); the chunk_read-raise-is-eval-integrity handling applies to the joint run
where synthesis rehydrates, not to this retrieval-only harness.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Sequence

from eval.acord import AcordQuery
from eval.harness import recall_at_k

# query text -> ranked corpus-ids, best first (the retrieve -> rerank -> fusion output)
Retrieve = Callable[[str], list[str]]


def ndcg_at_k(ranked: Sequence[str], graded: dict[str, int], k: int) -> float:
    """Graded nDCG@k with exponential gain (2^grade - 1); a doc absent from `graded` contributes 0."""

    def gain(corpus_id: str) -> float:
        return (2.0 ** graded.get(corpus_id, 0)) - 1.0

    dcg = sum(gain(cid) / math.log2(rank + 1) for rank, cid in enumerate(ranked[:k], start=1))
    ideal = sorted(graded.values(), reverse=True)
    idcg = sum((2.0**g - 1.0) / math.log2(rank + 1) for rank, g in enumerate(ideal[:k], start=1))
    return dcg / idcg if idcg > 0 else 0.0


@dataclass(frozen=True)
class AcordRetrievalReport:
    """Mean retrieval metrics over the test queries, plus per-query recall@50 for inspection."""

    n_queries: int
    recall_at_50: float  # the gate metric (>= 0.70 to pass)
    recall_at_10: float
    ndcg_at_10: float
    per_query_recall_at_50: dict[str, float]


def evaluate_retrieval(
    queries: list[AcordQuery], retrieve: Retrieve, *, k_gate: int = 50, k_secondary: int = 10
) -> AcordRetrievalReport:
    """Run the retrieval bar over `queries`: recall@{k_gate,k_secondary} (binary) + nDCG@k_secondary."""
    if not queries:
        raise ValueError("no queries to evaluate (empty relevant population)")
    per_q50: dict[str, float] = {}
    sum_r50 = sum_r10 = sum_ndcg = 0.0
    for q in queries:
        ranked = retrieve(q.text)
        r50 = recall_at_k(ranked, q.relevant, k_gate)
        per_q50[q.query_id] = r50
        sum_r50 += r50
        sum_r10 += recall_at_k(ranked, q.relevant, k_secondary)
        sum_ndcg += ndcg_at_k(ranked, q.graded, k_secondary)
    n = len(queries)
    return AcordRetrievalReport(
        n_queries=n,
        recall_at_50=sum_r50 / n,
        recall_at_10=sum_r10 / n,
        ndcg_at_10=sum_ndcg / n,
        per_query_recall_at_50=per_q50,
    )
