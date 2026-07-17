#!/usr/bin/env python
"""Diagnostic (T33): raw HYBRID recall ceiling (no rerank), to isolate retrieval vs rerank.

The dry-run recall@50 (after rerank) was 0.38, below the 0.70 gate, and nDCG@10 (0.138) is below ACORD's
published baselines — a red flag (ADR-0011: diagnose implausible retrieval numbers before reporting). This
measures the RAW hybrid pool recall@{10,50,100,200}: if the pool ceiling is high, rerank/the cut is losing
relevants; if the ceiling is also low, the dense+sparse retrieval itself is not surfacing them.

    PYTHONPATH=. uv run python scripts/diagnose_acord_retrieval.py
"""

from __future__ import annotations


from dotenv import load_dotenv

from eval.acord import load_test_queries
from eval.harness import recall_at_k
from rag_wright.store.arcadedb import ArcadeDBStore

ACORD_DB = "ragwright_acord"
POOL = 200


def _corpus_id(chunk_id: str) -> str:
    return chunk_id.rsplit(":", 2)[0]


class _CpuEmbedder:
    def __init__(self) -> None:
        from FlagEmbedding import BGEM3FlagModel

        self._m = BGEM3FlagModel("BAAI/bge-m3", use_fp16=False, devices=["cpu"])

    def dense_sparse(self, text: str):
        out = self._m.encode([text], return_dense=True, return_sparse=True, max_length=1024)
        dense = out["dense_vecs"][0].tolist()
        sparse = {int(k): float(v) for k, v in out["lexical_weights"][0].items()}
        return dense, sparse


def main() -> None:
    load_dotenv()
    queries = load_test_queries()
    store = ArcadeDBStore.from_env(database=ACORD_DB, reset=False)
    emb = _CpuEmbedder()
    ks = [10, 50, 100, 200]
    sums = {k: 0.0 for k in ks}
    for q in queries:
        dense, sparse = emb.dense_sparse(q.text)
        fused = store.hybrid_search(dense, sparse, k=POOL)
        ranked = [_corpus_id(r["chunk_id"]) for r in fused]
        for k in ks:
            sums[k] += recall_at_k(ranked, q.relevant, k)
    n = len(queries)
    print(f"\n[diagnose] raw HYBRID recall (no rerank), {n} queries, pool={POOL}:")
    for k in ks:
        print(f"  hybrid recall@{k:<3} = {sums[k] / n:.4f}")
    store.close()


if __name__ == "__main__":
    main()
