"""T58b stage 2: LISTWISE top-N re-order over the pointwise reranker's output.

The diagnostic showed the condensed top-10 loss is 2/3 genuine misrank (>=10 judged clauses strictly
outscore the gold) and 1/3 ties, concentrated in within-family/contrastive queries -- the failure mode of
POINTWISE scoring, which never sees the candidates side by side. This stage re-reads the condensed top-N
(judged-only, pointwise-ordered) TOGETHER against the discriminator and re-orders them, so the model can do
the contrastive judgment ("this caps the seller; that caps the buyer") pointwise scoring cannot.

Reuses the pointwise score cache (data/models/fullpipe_scores.jsonl) -- each query's exact run discriminator
is recovered by token-Jaccard to the query text (greedy unique assignment), so NO pointwise re-grading. Only
~57 listwise LLM calls. Condensed metrics before (pointwise) vs after (listwise). Model-swappable
(RERANK_MODEL), crash-safe listwise cache, X/N flushed progress.

  uv run python -m eval.listwise_rerank
  LISTWISE_N=30 uv run python -m eval.listwise_rerank
"""

from __future__ import annotations

import json
import os
import re
import statistics
import threading
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

from eval.acord import load_corpus, load_test_queries
from eval.acord_retrieval import ndcg_at_k
from eval.harness import recall_at_k
from rag_wright.models.profiles import profile_for
from rag_wright.models.seam import build_model
from rag_wright.store.arcadedb import SPAN_TYPE, ArcadeDBStore, _sql_str, _str_array
from rag_wright.util.concurrent import map_concurrent

DB = os.environ.get("PIVOT_DB", "ragwright_acord_pivot")
MODEL = os.environ.get("RERANK_MODEL", "google/gemma-4-31b-it")
N = int(os.environ.get("LISTWISE_N", "20"))
POINTWISE_CACHE = Path("data/models/fullpipe_scores.jsonl")
LISTWISE_CACHE = Path("data/models/listwise_orders.jsonl")
PROGRESS = Path("data/models/listwise_progress.log")
_TOK = re.compile(r"[a-z0-9]+")


def _tok(s: str) -> set[str]:
    return set(_TOK.findall(s.lower()))


def _jac(a: set[str], b: set[str]) -> float:
    return len(a & b) / max(1, len(a | b))


def _progress(msg: str) -> None:
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS.write_text(msg + "\n", encoding="utf-8")
    print(msg, flush=True)


class Ranking(BaseModel):
    order: list[int]  # candidate numbers, BEST match first


def _listwise_prompt(test: str, clauses: list[str]) -> str:
    body = "\n".join(f"[{i + 1}] {c[:600]}" for i, c in enumerate(clauses))
    return (
        "You are ranking contract clauses by how well each satisfies this decisive test:\n"
        f"{test}\n\n"
        f"Below are {len(clauses)} numbered candidate clauses. Compare them AGAINST EACH OTHER and order them "
        "from the BEST match to the WORST. A clause that fully and unambiguously satisfies the decisive test "
        "ranks above one that only partially or arguably satisfies it; break near-ties by which clause more "
        "directly and specifically satisfies the test (e.g. names the exact party, carve-out, or condition "
        "the test calls for).\n\n"
        f"Candidates:\n{body}\n\n"
        f"Output the candidate numbers in ranked order, best first. Include ALL {len(clauses)} numbers exactly "
        "once."
    )


def main() -> None:
    load_dotenv()
    store = ArcadeDBStore.from_env(database=DB, reset=False)
    corpus = {c.clause_id: c.text for c in load_corpus()}
    queries = load_test_queries()

    def oracle_f1(gold: list[str]) -> str | None:
        reach: dict[str, int] = {}
        for g in gold:
            for f in {r["function"] for r in store._query(
                    f"SELECT function FROM {SPAN_TYPE} WHERE parent_okf_path = {_sql_str(g)}")}:
                reach[f] = reach.get(f, 0) + 1
        return max(reach, key=reach.get) if reach else None

    def pool_for(fn: str | None) -> list[str]:
        return [] if not fn else list(dict.fromkeys(r["parent_okf_path"] for r in store._query(
            f"SELECT parent_okf_path FROM {SPAN_TYPE} WHERE primary_tag IN {_str_array([fn])}")))

    pool_of = {q.query_id: pool_for(oracle_f1(sorted(q.relevant))) for q in queries}

    # load pointwise scores; recover each query's exact run discriminator (test) by query-text Jaccard,
    # greedily and uniquely among the tests whose clause-set covers the query's pool (same-function group).
    by_test: dict[str, dict[str, float]] = defaultdict(dict)
    for line in POINTWISE_CACHE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            by_test[r["test"]][r["clause_id"]] = r["score"]
    test_tok = {t: _tok(t) for t in by_test}
    pairs = []  # (jaccard, query_id, test)
    for q in queries:
        ps = set(pool_of[q.query_id])
        qt = _tok(q.text)
        for t, d in by_test.items():
            if ps and ps <= d.keys():
                pairs.append((_jac(qt, test_tok[t]), q.query_id, t))
    pairs.sort(reverse=True)
    test_of: dict[str, str] = {}
    used_test: set[str] = set()
    for j, qid, t in pairs:
        if qid not in test_of and t not in used_test:
            test_of[qid] = t
            used_test.add(t)
    _progress(f"[map] {len(test_of)}/{len(queries)} queries mapped to a discriminator; "
              f"min Jaccard={min((_jac(_tok(q.text), test_tok[test_of[q.query_id]]) for q in queries if q.query_id in test_of), default=0):.2f}")

    # condensed top-N per query, in pointwise order (this is the listwise input window)
    windows: dict[str, list[str]] = {}
    for q in queries:
        t = test_of.get(q.query_id)
        if not t:
            windows[q.query_id] = []
            continue
        sc = by_test[t]
        judged = [c for c in pool_of[q.query_id] if c in q.graded]
        windows[q.query_id] = sorted(judged, key=lambda c: (-sc.get(c, 0.0), c))[:N]

    # listwise runnable (fast-fail + profile provider routing)
    prof = profile_for(MODEL)
    skw = {"method": prof.structured_method}
    if prof.structured_extra_body is not None:
        skw["extra_body"] = prof.structured_extra_body
    runnable = build_model(MODEL, timeout=40.0, max_retries=0).with_structured_output(Ranking, **skw)

    lock = threading.Lock()
    done: dict[tuple[str, str], list[str]] = {}
    if os.environ.get("FRESH") == "1":
        LISTWISE_CACHE.unlink(missing_ok=True)
    if LISTWISE_CACHE.exists():
        for line in LISTWISE_CACHE.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                done[(r["model"], r["test"])] = r["order"]

    def _reorder(win: list[str], order: list[int]) -> list[str]:
        # map 1-based candidate numbers -> clause ids; dedupe; append any omitted in pointwise order
        seen: list[str] = []
        for i in order:
            if 1 <= i <= len(win) and win[i - 1] not in seen:
                seen.append(win[i - 1])
        for c in win:
            if c not in seen:
                seen.append(c)
        return seen

    def listwise(qid: str) -> list[str]:
        win = windows[qid]
        t = test_of[qid]
        if len(win) <= 1:
            return win
        key = (MODEL, t)
        if key in done:
            return _reorder(win, done[key])
        order: list[int] = []
        for _ in range(3):
            try:
                v = runnable.invoke(_listwise_prompt(t, [corpus.get(c, "") for c in win]))
            except Exception:  # noqa: BLE001
                continue
            if v is not None:
                order = v.order
                break
        with lock:
            with LISTWISE_CACHE.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"model": MODEL, "test": t, "order": order}) + "\n")
                f.flush()
            done[key] = order
        return _reorder(win, order)

    todo = [q.query_id for q in queries if len(windows[q.query_id]) > 1]
    _progress(f"[listwise] {len(todo)} queries, top-{N} re-order via {MODEL}")
    new_windows = dict(zip(todo, map_concurrent(todo, listwise, max_concurrency=8,
                                                progress_path=PROGRESS, label="[listwise]", echo=True, every=5)))

    # metrics: pointwise (before) vs listwise (after), condensed
    def metrics(order_of):
        r10, r20, nd = [], [], []
        for q in queries:
            win = windows[q.query_id]
            if not win:
                continue
            cond = order_of(q)  # condensed ranking (judged-only)
            r10.append(recall_at_k(cond, q.relevant, 10))
            r20.append(recall_at_k(cond, q.relevant, 20))
            nd.append(ndcg_at_k(cond, q.graded, 10))
        return statistics.mean(r10), statistics.mean(r20), statistics.mean(nd)

    # pointwise condensed = FULL judged ranking (not just window) for a fair before/after at @10/@20
    def pointwise_order(q):
        t = test_of.get(q.query_id)
        sc = by_test[t]
        judged = [c for c in pool_of[q.query_id] if c in q.graded]
        return sorted(judged, key=lambda c: (-sc.get(c, 0.0), c))

    def listwise_order(q):
        # re-ordered top-N, then the pointwise tail (judged beyond N) appended
        head = new_windows.get(q.query_id, windows[q.query_id])
        tail = [c for c in pointwise_order(q) if c not in set(head)]
        return head + tail

    p10, p20, pnd = metrics(pointwise_order)
    l10, l20, lnd = metrics(listwise_order)
    print(f"\n=== LISTWISE top-{N} re-order | queries={sum(1 for q in queries if windows[q.query_id])} | "
          f"model={MODEL} (condensed) ===", flush=True)
    print("                recall@10   recall@20   nDCG@10", flush=True)
    print(f"pointwise       {p10:.3f}       {p20:.3f}       {pnd:.3f}", flush=True)
    print(f"+ listwise      {l10:.3f}       {l20:.3f}       {lnd:.3f}   "
          f"(dr@10={l10 - p10:+.3f}  dnDCG={lnd - pnd:+.3f})", flush=True)

    # per-query movers (nDCG@10 delta), flag contrastive queries
    CONTRA = ("favorable", "exception", "carve", "waiv", "affiliate", "first party", "third party",
              "mutual", "different", "seller", "buyer", "non-reliance", "unilateral")
    movers = []
    for q in queries:
        if not windows[q.query_id]:
            continue
        pn = ndcg_at_k(pointwise_order(q), q.graded, 10)
        ln = ndcg_at_k(listwise_order(q), q.graded, 10)
        movers.append((ln - pn, q.text, any(k in q.text.lower() for k in CONTRA)))
    up = [m for m in movers if m[0] > 0.02]
    dn = [m for m in movers if m[0] < -0.02]
    flat = [m for m in movers if abs(m[0]) <= 0.02]
    print(f"\nper-query nDCG@10 movement: up={len(up)}  flat={len(flat)}  down={len(dn)}", flush=True)
    print("  biggest improvements:", flush=True)
    for d, txt, c in sorted(movers, reverse=True)[:6]:
        print(f"    {d:+.3f} {'CONTRA' if c else 'plain '} {txt[:52]}", flush=True)
    print("  biggest regressions:", flush=True)
    for d, txt, c in sorted(movers)[:6]:
        print(f"    {d:+.3f} {'CONTRA' if c else 'plain '} {txt[:52]}", flush=True)
    store.close()


if __name__ == "__main__":
    main()
