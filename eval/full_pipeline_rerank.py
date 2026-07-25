"""T58b (a)+(b): full 57-query pipeline -- oracle function -> Gemma decompose (hardened discriminator) ->
Gemma rerank (continuous 0-1 score) -> (a) CONDENSED nDCG@10 + recall@10/@20 and (b) FULL-pool nDCG@10
(ACORD-comparable). Gemma throughout, crash-safe (per-grade cache, resumable), progress flushed + echoed.

Caveat: uses the ORACLE function per query to pick the pool (the function-gate ceiling is 0.94/0.99, measured
separately), so (b) reflects our reranker with correct routing -- indicative vs ACORD's no-oracle baselines,
not identical.

  uv run python -m eval.full_pipeline_rerank
  LIMIT=3 uv run python -m eval.full_pipeline_rerank    # dry-run over the first 3 queries
"""

from __future__ import annotations

import json
import os
import statistics
import threading
from pathlib import Path

from dotenv import load_dotenv

from eval.acord import load_corpus, load_test_queries
from eval.acord_retrieval import ndcg_at_k
from eval.ground_discriminator_rerank import Grade, _decompose_prompt, _grade_prompt
from eval.harness import recall_at_k
from rag_wright.models.profiles import profile_for
from rag_wright.models.seam import build_model
from rag_wright.store.arcadedb import SPAN_TYPE, ArcadeDBStore, _sql_str, _str_array
from rag_wright.util.concurrent import map_concurrent

DB = os.environ.get("PIVOT_DB", "ragwright_acord_pivot")
MODEL = os.environ.get("RERANK_MODEL", "google/gemma-4-31b-it")
DECOMP_MODEL = os.environ.get("DECOMP_MODEL", "google/gemma-4-31b-it")
LIMIT = int(os.environ.get("LIMIT", "0"))
PROGRESS = Path("data/models/fullpipe_progress.log")
CACHE = Path("data/models/fullpipe_scores.jsonl")


def _progress(msg: str) -> None:
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS.write_text(msg + "\n", encoding="utf-8")
    print(msg, flush=True)


def main() -> None:
    load_dotenv()
    store = ArcadeDBStore.from_env(database=DB, reset=False)
    corpus = {c.clause_id: c.text for c in load_corpus()}
    queries = load_test_queries()
    if LIMIT:
        queries = queries[:LIMIT]

    def oracle_f1(gold: list[str]) -> str | None:
        reach: dict[str, int] = {}
        for g in gold:
            for f in {r["function"] for r in store._query(
                    f"SELECT function FROM {SPAN_TYPE} WHERE parent_okf_path = {_sql_str(g)}")}:
                reach[f] = reach.get(f, 0) + 1
        return max(reach, key=reach.get) if reach else None

    def pool_for(fn: str | None) -> list[str]:
        if not fn:
            return []
        return list(dict.fromkeys(r["parent_okf_path"] for r in store._query(
            f"SELECT parent_okf_path FROM {SPAN_TYPE} WHERE function IN {_str_array([fn])}")))

    pool_of = {q.query_id: pool_for(oracle_f1(sorted(q.relevant))) for q in queries}

    # decompose (Gemma, hardened) -> discriminator per query
    dec_run = build_model(DECOMP_MODEL, temperature=0.0)

    def _dec(q):
        try:
            return dec_run.invoke(_decompose_prompt(q.text)).content.strip()
        except Exception:  # noqa: BLE001
            return q.text

    _progress(f"[decompose] {len(queries)} queries via {DECOMP_MODEL}")
    disc_of = dict(zip([q.query_id for q in queries],
                       map_concurrent(queries, _dec, max_concurrency=8, label="[decompose]", echo=True)))

    # grade runnable (fast-fail + profile provider routing)
    prof = profile_for(MODEL)
    skw = {"method": prof.structured_method}
    if prof.structured_extra_body is not None:
        skw["extra_body"] = prof.structured_extra_body
    runnable = build_model(MODEL, timeout=25.0, max_retries=0).with_structured_output(Grade, **skw)

    lock = threading.Lock()
    done: dict[tuple[str, str, str], float] = {}
    if os.environ.get("FRESH") == "1":
        CACHE.unlink(missing_ok=True)
    if CACHE.exists():
        for line in CACHE.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                done[(r["model"], r["test"], r["clause_id"])] = r["score"]
    _progress(f"[resume] {len(done)} scores cached")

    def _score(test: str, clause: str) -> float:
        for _ in range(3):
            try:
                v = runnable.invoke(_grade_prompt(test, clause))
            except Exception:  # noqa: BLE001
                continue
            if v is not None:
                return max(0.0, min(1.0, float(v.score)))
        return 0.0

    jobs = [(q.query_id, c) for q in queries for c in pool_of[q.query_id]]

    def _grade_job(job: tuple[str, str]) -> float:
        qid, c = job
        test = disc_of[qid]
        key = (MODEL, test, c)
        if key in done:
            return done[key]
        s = _score(test, corpus.get(c, ""))
        with lock:
            with CACHE.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"model": MODEL, "test": test, "clause_id": c, "score": s}) + "\n")
                f.flush()
            done[key] = s
        return s

    _progress(f"[rerank] {len(jobs)} (query,clause) grades via {MODEL}")
    scores = map_concurrent(jobs, _grade_job, max_concurrency=8, progress_path=PROGRESS, label="[rerank]",
                            echo=True, every=25)
    score_of = {job: s for job, s in zip(jobs, scores)}

    b_ndcg, a_ndcg, a_r10, a_r20, full_r50 = [], [], [], [], []
    for q in queries:
        pool = pool_of[q.query_id]
        if not pool:
            for lst in (b_ndcg, a_ndcg, a_r10, a_r20, full_r50):
                lst.append(0.0)
            continue
        ranked = sorted(pool, key=lambda c: (-score_of[(q.query_id, c)], c))
        cond = [c for c in ranked if c in q.graded]
        b_ndcg.append(ndcg_at_k(ranked, q.graded, 10))
        a_ndcg.append(ndcg_at_k(cond, q.graded, 10))
        a_r10.append(recall_at_k(cond, q.relevant, 10))
        a_r20.append(recall_at_k(cond, q.relevant, 20))
        full_r50.append(recall_at_k(ranked, q.relevant, 50))

    print(f"\n=== FULL PIPELINE  queries={len(queries)}  reranker={MODEL} (continuous score, oracle function) ===",
          flush=True)
    print(f"(b) FULL-pool nDCG@10  = {statistics.mean(b_ndcg):.3f}", flush=True)
    print(f"(a) CONDENSED nDCG@10  = {statistics.mean(a_ndcg):.3f}   recall@10={statistics.mean(a_r10):.3f}   "
          f"recall@20={statistics.mean(a_r20):.3f}", flush=True)
    print(f"    full recall@50 (confounded, ref) = {statistics.mean(full_r50):.3f}", flush=True)
    print("ACORD published nDCG@10 baselines: BM25 0.540 | MiniLM 0.572 | OpenAI-L 0.641 | GPT4o-rerank 0.812",
          flush=True)
    store.close()


if __name__ == "__main__":
    main()
