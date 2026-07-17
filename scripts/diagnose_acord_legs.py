#!/usr/bin/env python
"""Diagnostic (T33): dense-vs-sparse leg isolation + a length-annotated spot-check.

Isolates the retrieval failure class behind the below-baseline dry-run (recall@50=0.379, nDCG@10=0.138):

  1. LEG ISOLATION (highest-probability bug): dense-only vs sparse-only vs fused recall@{50,100} + nDCG@10,
     replicating the store's own `vector.neighbors` / `vector.sparseNeighbors` legs. Tells us whether one
     leg is dead weight, both are weak, or fusion is worse than the better leg alone (a fusion bug).
  2. SPOT-CHECK: for a few queries, the top-10 fused hits with clause LENGTH and relevance noted, so we can
     see topical plausibility (bug vs genuine difficulty) and whether relevant clauses skew long and rank low
     (embedding-weak-on-long-content).

    PYTHONPATH=. uv run python scripts/diagnose_acord_legs.py
"""

from __future__ import annotations

from dotenv import load_dotenv

from eval.acord import load_corpus, load_test_queries
from eval.acord_retrieval import ndcg_at_k
from eval.harness import recall_at_k
from rag_wright.store.arcadedb import _DENSE_INDEX, _SPARSE_INDEX, _float_array, ArcadeDBStore

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


def _dense_only(store, dense, k):
    sql = (
        f"SELECT chunk_id, source_doc_id FROM "
        f"(SELECT expand(`vector.neighbors`('{_DENSE_INDEX}', {_float_array(dense)}, {k}))) LIMIT {k}"
    )
    return [_corpus_id(r["chunk_id"]) for r in store._query(sql)]


def _sparse_only(store, sparse, k):
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
    corpus = {c.clause_id: c.text for c in load_corpus()}
    store = ArcadeDBStore.from_env(database=ACORD_DB, reset=False)
    emb = _CpuEmbedder()

    legs = {"dense": {}, "sparse": {}, "fused": {}}
    for name in legs:
        legs[name] = {"r50": 0.0, "r100": 0.0, "ndcg10": 0.0}

    spot = []
    for i, q in enumerate(queries):
        dense, sparse = emb.dense_sparse(q.text)
        ranked = {
            "dense": _dense_only(store, dense, POOL),
            "sparse": _sparse_only(store, sparse, POOL),
            "fused": [_corpus_id(r["chunk_id"]) for r in store.hybrid_search(dense, sparse, k=POOL)],
        }
        for name, r in ranked.items():
            legs[name]["r50"] += recall_at_k(r, q.relevant, 50)
            legs[name]["r100"] += recall_at_k(r, q.relevant, 100)
            legs[name]["ndcg10"] += ndcg_at_k(r, q.graded, 10)
        if i < 4:  # spot-check the first few
            spot.append((q, ranked["fused"]))

    n = len(queries)
    print(f"\n[legs] dense-vs-sparse-vs-fused, {n} queries, pool={POOL}:")
    print(f"  {'leg':<7} {'recall@50':>10} {'recall@100':>11} {'nDCG@10':>9}")
    for name in ("dense", "sparse", "fused"):
        m = legs[name]
        print(f"  {name:<7} {m['r50']/n:>10.4f} {m['r100']/n:>11.4f} {m['ndcg10']/n:>9.4f}")

    print("\n[spot-check] top-10 fused hits (L=chars, *=relevant grade>=2):")
    rel_lens = [len(corpus[c]) for q, _ in spot for c in q.relevant if c in corpus]
    print(f"  relevant-clause length: mean={sum(rel_lens)//len(rel_lens)}c over {len(rel_lens)} relevants")
    for q, fused in spot:
        first_rel = next((rank for rank, c in enumerate(fused, 1) if c in q.relevant), None)
        print(f"\n  QUERY: {q.text!r}  ({len(q.relevant)} relevant; first relevant at rank {first_rel})")
        for rank, c in enumerate(fused[:10], 1):
            rel = "*" if c in q.relevant else " "
            text = corpus.get(c, "")
            print(f"    {rank:>2}{rel} L={len(text):>5}  {text[:80]!r}")
    store.close()


if __name__ == "__main__":
    main()
