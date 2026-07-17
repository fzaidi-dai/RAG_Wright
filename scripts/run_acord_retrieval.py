#!/usr/bin/env python
"""ACORD retrieval-bar DRY RUN (T33): retrieve -> rerank -> fusion, recall@50/@10 + nDCG@10, real store.

Confirms recall@50 >= 0.70 is achievable before the graded run locks (the pinned
recalibrate-before-never-after discipline). Retrieval legs only — chunk_read / synthesis is the answer
half (GraphWright's) — scored against the grade->=2 test qrels. The reranker needs passage text, rehydrated
from the T40 sidecar; a miss there is the same invariant violation the eval-integrity handling names
(indexed but no sidecar text), so it is raised loud, not silently dropped.

    PYTHONPATH=. uv run python scripts/run_acord_retrieval.py
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

from eval.acord import load_test_queries
from eval.acord_retrieval import evaluate_retrieval
from rag_wright.capabilities.embedding import BGEM3Embedder
from rag_wright.capabilities.fusion import fuse
from rag_wright.capabilities.graph_query import GraphAnswer
from rag_wright.capabilities.reranking import BGEReranker, Passage, rerank
from rag_wright.store.arcadedb import ArcadeDBStore
from rag_wright.store.chunk_text import ChunkTextStore

ACORD_DB = "ragwright_acord"
TEXT_DIR = Path("data/acord/chunk_text")
HYBRID_K = 100  # hybrid candidate pool per query (the rerank pool)
RERANK_TOPK = 100  # rerank the whole pool — recall@50 needs >= 50 ranked
FUSION_CAP = 100  # empty graph leg -> fusion is reranked passthrough, capped

_EMPTY_GRAPH = GraphAnswer(start_entity_id="", relationship_type="", evidence=[])


def _corpus_id(chunk_id: str) -> str:
    return chunk_id.rsplit(":", 2)[0]  # <source_doc_id>:<chunk_index>:<content_hash> -> ACORD _id


def main() -> None:
    load_dotenv()
    queries = load_test_queries()
    print(f"[dry-run] {len(queries)} ACORD test queries (grade>=2 relevant sets)")

    store = ArcadeDBStore.from_env(database=ACORD_DB, reset=False)
    text_store = ChunkTextStore(TEXT_DIR)
    print(f"[dry-run] store chunk_count = {store.chunk_count()}")
    embedder = BGEM3Embedder()
    reranker = BGEReranker()

    def retrieve(query_text: str) -> list[str]:
        dense = embedder.encode_dense(query_text)
        sparse = embedder.encode_sparse(query_text)
        fused = store.hybrid_search(dense, sparse, k=HYBRID_K)
        passages: list[Passage] = []
        for row in fused:
            text = text_store.get(row["chunk_id"])
            if text is None:
                raise RuntimeError(
                    f"EVAL-INTEGRITY: no sidecar text for indexed chunk_id {row['chunk_id']!r} — the "
                    "indexed-implies-sidecar-text invariant is violated (ingest/sidecar bug)"
                )
            passages.append(
                Passage(chunk_id=row["chunk_id"], source_doc_id=row["source_doc_id"], text=text)
            )
        reranked = rerank(query_text, passages, reranker=reranker, top_k=RERANK_TOPK)
        result = fuse(reranked, _EMPTY_GRAPH, cap=FUSION_CAP)
        return [_corpus_id(c.chunk_id) for c in result.chunks]

    report = evaluate_retrieval(queries, retrieve)
    print(f"\n[dry-run] ACORD retrieval bar (test split, {report.n_queries} queries, pool k={HYBRID_K}):")
    print(f"  recall@50 (GATE, grade>=2)   = {report.recall_at_50:.4f}   [threshold >= 0.70]")
    print(f"  recall@10 (reported)         = {report.recall_at_10:.4f}")
    print(f"  nDCG@10   (graded, reported) = {report.ndcg_at_10:.4f}")
    verdict = "PASS" if report.recall_at_50 >= 0.70 else "BELOW THRESHOLD — recalibrate before graded run"
    print(f"  => recall@50 {report.recall_at_50:.4f}: {verdict}")
    store.close()


if __name__ == "__main__":
    main()
