#!/usr/bin/env python
"""T41 mechanism proof (fair): semantic-kNN expansion + the cross-encoder RERANKER.

RRF fusion of the graph leg hurt recall (it ranks expansion by anchor-cosine, amplifying noise from
irrelevant anchors). The real pipeline is retrieve->rerank->fusion: the cross-encoder is the query-relevance
precision gate RRF lacks. This reranks the two-leg pool vs the graph-augmented pool with the SAME reranker,
so the delta isolates whether the graph leg's added candidates survive a query-relevance gate.

    PYTHONPATH=. uv run python scripts/diagnose_acord_knn_rerank.py
"""

from __future__ import annotations

import numpy as np
from dotenv import load_dotenv

from eval.acord import load_test_queries
from eval.acord_retrieval import ndcg_at_k
from eval.harness import recall_at_k
from rag_wright.store.arcadedb import ArcadeDBStore
from rag_wright.store.chunk_text import ChunkTextStore

ACORD_DB = "ragwright_acord"
TEXT_DIR = "data/acord/chunk_text"
BASE = 50  # two-leg hybrid pool (also the rerank pool for two-leg)
ANCHORS = 10
E = 5  # neighbors per anchor -> expansion adds up to ANCHORS*E candidates


def _corpus_id(chunk_id: str) -> str:
    return chunk_id.rsplit(":", 2)[0]


class _CpuReranker:
    def __init__(self) -> None:
        from FlagEmbedding import FlagAutoReranker

        self._m = FlagAutoReranker.from_finetuned("BAAI/bge-reranker-v2-m3", use_fp16=False, devices=["cpu"])

    def rank(self, query: str, ids: list[str], text: dict[str, str]) -> list[str]:
        if not ids:
            return []
        scores = self._m.compute_score([(query, text[c]) for c in ids], max_length=512, batch_size=16)
        if not isinstance(scores, (list, tuple)):
            scores = [scores]
        return [c for _, c in sorted(zip(scores, ids), key=lambda p: -p[0])]


def main() -> None:
    load_dotenv()
    from FlagEmbedding import BGEM3FlagModel

    queries = load_test_queries()
    store = ArcadeDBStore.from_env(database=ACORD_DB, reset=False)
    text_store = ChunkTextStore(TEXT_DIR)

    rows = store._query("SELECT chunk_id, dense FROM Chunk")
    ids = [_corpus_id(r["chunk_id"]) for r in rows]
    idx = {c: i for i, c in enumerate(ids)}
    V = np.asarray([r["dense"] for r in rows], dtype=np.float32)
    V /= np.linalg.norm(V, axis=1, keepdims=True) + 1e-9

    model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=False, devices=["cpu"])
    enc = model.encode([q.text for q in queries], batch_size=16, max_length=1024,
                       return_dense=True, return_sparse=True)
    q_dense = [v.tolist() for v in enc["dense_vecs"]]
    q_sparse = [{int(k): float(v) for k, v in lw.items()} for lw in enc["lexical_weights"]]
    reranker = _CpuReranker()

    two_r50, two_r10, two_nd, three_r50, three_r10, three_nd = ([] for _ in range(6))
    for i, q in enumerate(queries):
        base = [_corpus_id(r["chunk_id"]) for r in store.hybrid_search(q_dense[i], q_sparse[i], k=BASE)]
        anchors = [c for c in base[:ANCHORS] if c in idx]
        expanded: set[str] = set()
        if anchors:
            sims = V[[idx[a] for a in anchors]] @ V.T
            for row in sims:
                for j in np.argpartition(-row, E + 1)[: E + 1]:
                    expanded.add(ids[j])
        pool3 = list(dict.fromkeys(base + [c for c in expanded if c not in base]))
        txt = {c: (text_store.get(c) or "") for c in pool3}
        ranked2 = reranker.rank(q.text, base, txt)
        ranked3 = reranker.rank(q.text, pool3, txt)
        two_r50.append(recall_at_k(ranked2, q.relevant, 50))
        three_r50.append(recall_at_k(ranked3, q.relevant, 50))
        two_r10.append(recall_at_k(ranked2, q.relevant, 10))
        three_r10.append(recall_at_k(ranked3, q.relevant, 10))
        two_nd.append(ndcg_at_k([c for c in ranked2 if c in q.graded], q.graded, 10))
        three_nd.append(ndcg_at_k([c for c in ranked3 if c in q.graded], q.graded, 10))
        print(f"[rr] {i + 1}/{len(queries)} pool2={len(base)} pool3={len(pool3)}", flush=True)

    print(f"\n[knn+rerank] BASE={BASE} ANCHORS={ANCHORS} E={E}. Grounded bar recall@50 >= 0.667")
    print(f"  two-leg + rerank    recall@50={np.mean(two_r50):.3f}  recall@10={np.mean(two_r10):.3f}  pool-nDCG@10={np.mean(two_nd):.3f}")
    print(f"  three-leg + rerank  recall@50={np.mean(three_r50):.3f}  recall@10={np.mean(three_r10):.3f}  pool-nDCG@10={np.mean(three_nd):.3f}")
    store.close()


if __name__ == "__main__":
    main()
