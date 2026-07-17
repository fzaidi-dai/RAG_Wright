#!/usr/bin/env python
"""T41 mechanism proof: semantic-kNN clause graph + clause-anchored expansion, three-leg recall.

Tests the hypothesis the relevance-structure analysis PROVED (0.706 missed->anchor cosine): does expanding
the candidate set along clause-clause similarity edges from the retrieved anchors recover REAL recall (not
just reachability — expansion adds neighbors of irrelevant anchors too, honest noise). Builds the kNN graph
from the stored dense vectors (no re-embed), expands each query's hybrid anchors, RRF-fuses the graph leg
with the two-leg hybrid, and reports two-leg vs three-leg recall@50/@10 and pool-ranking nDCG@10.

Judged against the grounded bar recall@50 >= 0.667 (E/0.75, PIN 3).

    PYTHONPATH=. uv run python scripts/diagnose_acord_knn_expansion.py
"""

from __future__ import annotations

import time

import numpy as np
from dotenv import load_dotenv

from eval.acord import load_test_queries
from eval.acord_retrieval import ndcg_at_k
from eval.harness import recall_at_k
from rag_wright.store.arcadedb import ArcadeDBStore

ACORD_DB = "ragwright_acord"
BASE = 100  # hybrid candidate pool (two-leg)
ANCHORS = 50  # expand from the top-N hybrid results
RRF_K = 60
E_VALUES = [5, 10, 20]  # neighbors per anchor to test


def _corpus_id(chunk_id: str) -> str:
    return chunk_id.rsplit(":", 2)[0]


def _rrf(hybrid: list[str], graph: list[str]) -> list[str]:
    hr = {c: i for i, c in enumerate(hybrid)}
    gr = {c: i for i, c in enumerate(graph)}
    docs = set(hybrid) | set(graph)
    score = {d: (1.0 / (RRF_K + hr[d]) if d in hr else 0.0) + (1.0 / (RRF_K + gr[d]) if d in gr else 0.0)
             for d in docs}
    return sorted(docs, key=lambda d: -score[d])


def main() -> None:
    load_dotenv()
    from FlagEmbedding import BGEM3FlagModel

    queries = load_test_queries()
    store = ArcadeDBStore.from_env(database=ACORD_DB, reset=False)

    t = time.time()
    rows = store._query("SELECT chunk_id, dense FROM Chunk")
    ids = [_corpus_id(r["chunk_id"]) for r in rows]
    idx = {c: i for i, c in enumerate(ids)}
    V = np.asarray([r["dense"] for r in rows], dtype=np.float32)
    V /= np.linalg.norm(V, axis=1, keepdims=True) + 1e-9
    print(f"[knn] fetched {len(ids)} clause vectors in {time.time() - t:.1f}s")

    model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=False, devices=["cpu"])
    enc = model.encode([q.text for q in queries], batch_size=16, max_length=1024,
                       return_dense=True, return_sparse=True)
    q_dense = [v.tolist() for v in enc["dense_vecs"]]
    q_sparse = [{int(k): float(v) for k, v in lw.items()} for lw in enc["lexical_weights"]]

    # cache each query's hybrid ranking + anchors once
    hyb = []
    for i in range(len(queries)):
        h = [_corpus_id(r["chunk_id"]) for r in store.hybrid_search(q_dense[i], q_sparse[i], k=BASE)]
        hyb.append(h)

    def _report(name: str, ranked_lists: list[list[str]]) -> None:
        r50 = np.mean([recall_at_k(ranked_lists[i], queries[i].relevant, 50) for i in range(len(queries))])
        r10 = np.mean([recall_at_k(ranked_lists[i], queries[i].relevant, 10) for i in range(len(queries))])
        nd = np.mean([ndcg_at_k([c for c in ranked_lists[i] if c in queries[i].graded], queries[i].graded, 10)
                      for i in range(len(queries))])
        print(f"  {name:<24} recall@50={r50:.3f}  recall@10={r10:.3f}  pool-nDCG@10={nd:.3f}")

    print(f"\n[knn] two-leg vs three-leg (BASE={BASE}). Grounded bar recall@50 >= 0.667")
    _report("two-leg (hybrid)", hyb)
    for A in (5, 10, 20, 50):
        for E in E_VALUES:
            three = []
            for i in range(len(queries)):
                anchors = [c for c in hyb[i][:A] if c in idx]
                avecs = V[[idx[a] for a in anchors]]
                sims = avecs @ V.T
                gscore: dict[str, float] = {}
                for row in sims:
                    for j in np.argpartition(-row, E + 1)[: E + 1]:
                        cid = ids[j]
                        if cid in anchors:
                            continue
                        s = float(row[j])
                        if s > gscore.get(cid, -1.0):
                            gscore[cid] = s
                graph_list = sorted(gscore, key=lambda c: -gscore[c])
                three.append(_rrf(hyb[i], graph_list))
            _report(f"three-leg (A={A},E={E})", three)
    store.close()


if __name__ == "__main__":
    main()
