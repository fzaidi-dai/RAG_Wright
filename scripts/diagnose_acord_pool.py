#!/usr/bin/env python
"""Diagnostic (T33): POOL-RANKING nDCG vs ACORD's published baselines (the like-for-like test).

ACORD explicitly annotates ~1,088 clauses PER QUERY (61,988 judged / 57), including grade-0/1 negatives.
Their published retrieval-only baselines (Table 3) are nDCG@10: BM25 54.0, MiniLM bi-encoder 57.2,
OpenAI-large 64.1. My FULL-CORPUS retrieval (rank all 3,931) got nDCG@10 ~0.10 — 5-6x below BM25, which is
implausible for BGE-M3 given the spot-check showed topically-plausible hits. The likely cause is a task
mismatch: their baselines rank the per-query judged POOL, I ranked the full corpus.

This settles it empirically without interpreting the paper: rank ONLY each query's judged clauses by my
retriever's order (filter the full-corpus ranking to the judged set) and compute nDCG@5/@10. If my
pool-ranking nDCG@10 lands near 54-64, retrieval is HEALTHY and the full-corpus number is just the harder
task; if it's still ~10, retrieval is genuinely broken.

    PYTHONPATH=. uv run python scripts/diagnose_acord_pool.py
"""

from __future__ import annotations

from dotenv import load_dotenv

from eval.acord import load_test_queries
from eval.acord_retrieval import ndcg_at_k
from rag_wright.store.arcadedb import ArcadeDBStore

ACORD_DB = "ragwright_acord"
COVER = 3931  # rank the whole corpus so every judged clause gets a position


def _corpus_id(chunk_id: str) -> str:
    return chunk_id.rsplit(":", 2)[0]


class _CpuEmbedder:
    def __init__(self) -> None:
        from FlagEmbedding import BGEM3FlagModel

        self._m = BGEM3FlagModel("BAAI/bge-m3", use_fp16=False, devices=["cpu"])

    def dense_sparse(self, text: str):
        out = self._m.encode([text], return_dense=True, return_sparse=True, max_length=1024)
        return out["dense_vecs"][0].tolist(), {int(k): float(v) for k, v in out["lexical_weights"][0].items()}


def main() -> None:
    load_dotenv()
    queries = load_test_queries()
    store = ArcadeDBStore.from_env(database=ACORD_DB, reset=False)
    emb = _CpuEmbedder()

    sums = {"ndcg5": 0.0, "ndcg10": 0.0}
    for q in queries:
        dense, sparse = emb.dense_sparse(q.text)
        full = [_corpus_id(r["chunk_id"]) for r in store.hybrid_search(dense, sparse, k=COVER)]
        pool_ranked = [cid for cid in full if cid in q.graded]  # my retriever's order over the judged pool
        sums["ndcg5"] += ndcg_at_k(pool_ranked, q.graded, 5)
        sums["ndcg10"] += ndcg_at_k(pool_ranked, q.graded, 10)
    n = len(queries)
    print(f"\n[pool] fused retriever ranking the per-query judged pool, {n} queries:")
    print(f"  nDCG@5  = {sums['ndcg5'] / n:.4f}   (ACORD baselines: BM25 0.525, MiniLM 0.571, OpenAI-L 0.621)")
    print(f"  nDCG@10 = {sums['ndcg10'] / n:.4f}   (ACORD baselines: BM25 0.540, MiniLM 0.572, OpenAI-L 0.641)")
    store.close()


if __name__ == "__main__":
    main()
