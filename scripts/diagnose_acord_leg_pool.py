#!/usr/bin/env python
"""Diagnostic (T33): per-leg full-corpus recall@50 AND pool-ranking nDCG@10 together.

Settles two questions with one run: (1) which leg is actually dragging fusion (dense or sparse), and
(2) each leg's health on the ACORD-comparable pool-ranking metric (baselines nDCG@10: BM25 0.540,
MiniLM 0.572, OpenAI-L 0.641). Per query, per leg: full-corpus recall@50 = top-50 of the leg's ranking;
pool-ranking nDCG@10 = the leg's order restricted to the query's judged clauses.

    PYTHONPATH=. uv run python scripts/diagnose_acord_leg_pool.py
"""

from __future__ import annotations

from dotenv import load_dotenv

from eval.acord import load_test_queries
from eval.acord_retrieval import ndcg_at_k
from eval.harness import recall_at_k
from rag_wright.store.arcadedb import _DENSE_INDEX, _SPARSE_INDEX, _float_array, ArcadeDBStore

ACORD_DB = "ragwright_acord"
COVER = 3931


def _corpus_id(chunk_id: str) -> str:
    return chunk_id.rsplit(":", 2)[0]


class _CpuEmbedder:
    def __init__(self) -> None:
        from FlagEmbedding import BGEM3FlagModel

        self._m = BGEM3FlagModel("BAAI/bge-m3", use_fp16=False, devices=["cpu"])

    def dense_sparse(self, text: str):
        out = self._m.encode([text], return_dense=True, return_sparse=True, max_length=1024)
        return out["dense_vecs"][0].tolist(), {int(k): float(v) for k, v in out["lexical_weights"][0].items()}


def _dense(store, dense, k):
    sql = (
        f"SELECT chunk_id, source_doc_id FROM "
        f"(SELECT expand(`vector.neighbors`('{_DENSE_INDEX}', {_float_array(dense)}, {k}))) LIMIT {k}"
    )
    return [_corpus_id(r["chunk_id"]) for r in store._query(sql)]


def _sparse(store, sparse, k):
    ids = sorted(sparse)
    indices = "[" + ",".join(str(i) for i in ids) + "]"
    weights = _float_array(sparse[i] for i in ids)
    sql = (
        f"SELECT chunk_id, source_doc_id FROM "
        f"(SELECT expand(`vector.sparseNeighbors`('{_SPARSE_INDEX}', {indices}, {weights}, {k}))) LIMIT {k}"
    )
    return [_corpus_id(r["chunk_id"]) for r in store._query(sql)]


def main() -> None:
    load_dotenv()
    queries = load_test_queries()
    store = ArcadeDBStore.from_env(database=ACORD_DB, reset=False)
    emb = _CpuEmbedder()
    agg = {leg: {"recall50": 0.0, "poolndcg10": 0.0} for leg in ("dense", "sparse", "fused")}
    for q in queries:
        dense, sparse = emb.dense_sparse(q.text)
        rankings = {
            "dense": _dense(store, dense, COVER),
            "sparse": _sparse(store, sparse, COVER),
            "fused": [_corpus_id(r["chunk_id"]) for r in store.hybrid_search(dense, sparse, k=COVER)],
        }
        for leg, full in rankings.items():
            agg[leg]["recall50"] += recall_at_k(full, q.relevant, 50)
            pool = [cid for cid in full if cid in q.graded]
            agg[leg]["poolndcg10"] += ndcg_at_k(pool, q.graded, 10)
    n = len(queries)
    print(f"\n[leg+pool] {n} queries. ACORD pool-ranking nDCG@10 baselines: BM25 0.540 / MiniLM 0.572 / OpenAI-L 0.641")
    print(f"  {'leg':<7} {'full-corpus recall@50':>22} {'pool-ranking nDCG@10':>22}")
    for leg in ("dense", "sparse", "fused"):
        m = agg[leg]
        print(f"  {leg:<7} {m['recall50']/n:>22.4f} {m['poolndcg10']/n:>22.4f}")
    store.close()


if __name__ == "__main__":
    main()
