"""T58b: does function-filter + BGE-rerank REALIZE the single-function ceiling? (throttle-immune, local)

The ceiling (0.939 single / 0.992 union-2) says gold is REACHABLE via the function bucket. This tests
whether we can rank it into the top-50: oracle function per query (best-single, same as the ceiling), a
PURE function filter over ALL function-F clauses (not similarity-gated -- so it does not re-introduce the
reachability<->rankability problem), then BGE cross-encoder rerank against the clause text. recall@50 vs the
0.667 bar and the 0.379 two-leg baseline. No DeepSeek (query->function is stubbed by the oracle; the only
models are local BGE), so it is immune to the DeepSeek throttle and needs no property graph.

Progress is written FLUSHED to `data/models/rerank_progress.log` (tail it) -- per-query, with a running
recall -- so a long rerank is never a black box.

  uv run python -m eval.function_rerank
"""

from __future__ import annotations

import os
import statistics
import time
from pathlib import Path

from dotenv import load_dotenv

from eval.acord import load_corpus, load_test_queries
from eval.harness import recall_at_k
from rag_wright.capabilities.reranking import BGEReranker
from rag_wright.store.arcadedb import SPAN_TYPE, ArcadeDBStore, _sql_str, _str_array

DB = os.environ.get("PIVOT_DB", "ragwright_acord_pivot")
PROGRESS = Path("data/models/rerank_progress.log")


def _progress(msg: str) -> None:
    """Flushed heartbeat to the progress file AND stdout, so progress is visible, not buffered."""
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS.write_text(msg + "\n", encoding="utf-8")
    print(msg, flush=True)


def _oracle_functions(store: ArcadeDBStore, gold: list[str]) -> tuple[str | None, str | None]:
    """The best-single and 2nd function by gold reach (identical to the ceiling's oracle)."""
    reach: dict[str, int] = {}
    for g in gold:
        rows = store._query(f"SELECT function FROM {SPAN_TYPE} WHERE parent_okf_path = {_sql_str(g)}")
        for f in {r["function"] for r in rows}:
            reach[f] = reach.get(f, 0) + 1
    ranked = sorted(reach.items(), key=lambda kv: -kv[1])
    f1 = ranked[0][0] if ranked else None
    f2 = ranked[1][0] if len(ranked) > 1 else None
    return f1, f2


def _clauses_in_functions(store: ArcadeDBStore, functions: list[str]) -> list[str]:
    """All distinct clause ids whose spans carry any of `functions` (the PURE function filter, no ranking)."""
    fs = [f for f in functions if f]
    if not fs:
        return []
    rows = store._query(f"SELECT parent_okf_path FROM {SPAN_TYPE} WHERE primary_tag IN {_str_array(fs)}")
    return list(dict.fromkeys(r["parent_okf_path"] for r in rows))  # dedup, order-stable


def main() -> None:
    load_dotenv()
    store = ArcadeDBStore.from_env(database=DB, reset=False)
    corpus = {c.clause_id: c.text for c in load_corpus()}
    queries = load_test_queries()
    _progress(f"[setup] corpus={len(corpus)} queries={len(queries)}  loading BGE reranker (first use downloads)...")
    t_load = time.perf_counter()
    reranker = BGEReranker()
    _progress(f"[setup] reranker loaded in {time.perf_counter()-t_load:.0f}s; starting measurement")

    def measure(label: str, union2: bool) -> tuple[float, float, float, list[float]]:
        r50s, r10s, pools = [], [], []
        t0 = time.perf_counter()
        for i, q in enumerate(queries, 1):
            gold = sorted(q.relevant)
            f1, f2 = _oracle_functions(store, gold)
            cids = _clauses_in_functions(store, [f1, f2] if (union2 and f2) else [f1])
            if not cids:
                r50s.append(0.0)
                r10s.append(0.0)
                pools.append(0)
            else:
                scores = reranker.score(q.text, [corpus.get(c, "") for c in cids])
                ranked = [c for c, _ in sorted(zip(cids, scores), key=lambda cs: -cs[1])]
                r50s.append(recall_at_k(ranked, q.relevant, 50))
                r10s.append(recall_at_k(ranked, q.relevant, 10))
                pools.append(len(cids))
            _progress(f"{label} q {i}/{len(queries)}  pool={pools[-1]}  "
                      f"running recall@50={statistics.mean(r50s):.3f}  elapsed={time.perf_counter()-t0:.0f}s")
        return statistics.mean(r50s), statistics.mean(r10s), statistics.mean(pools), r50s

    results = []
    for label, u2 in [("[single]", False), ("[union2]", True)]:
        results.append((label, *measure(label, u2)))

    print(f"\n=== queries={len(queries)}  bar=0.667  baseline=0.379  ceiling: single 0.939 / union2 0.992 ===",
          flush=True)
    for label, r50, r10, pool, r50s in results:
        print(f"{label:9s} recall@50={r50:.3f}  recall@10={r10:.3f}  avg pool={pool:.0f}  "
              f"(queries<0.667: {sum(1 for r in r50s if r < 0.667)}/{len(r50s)})", flush=True)
    store.close()


if __name__ == "__main__":
    main()
