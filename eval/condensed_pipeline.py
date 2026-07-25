"""T58b condensed pipeline (clean harness): pointwise -> listwise, judged-only, fully reproducible.

The condensed metric only needs the JUDGED clauses (the 17,715-grade full run wasted work grading un-judged
clauses it then discards). This harness grades only `judged n pool` per query (~5-6k grades), and -- the fix
for the earlier reproducibility gap -- PERSISTS the per-query discriminator so pointwise and the listwise
re-order share the exact same test, and re-runs are deterministic and cache-stable.

Stages per query: oracle function -> pool -> judged = pool n graded. Gemma decompose -> discriminator
(persisted). Gemma pointwise continuous grade of each judged clause (cached by query_id+clause). Gemma
listwise re-order of the pointwise top-N. Condensed recall@10/@20 + nDCG@10, pointwise vs listwise, with
per-query movers. Model-swappable (RERANK_MODEL/DECOMP_MODEL), crash-safe, X/N flushed progress.

  uv run python -m eval.condensed_pipeline
  FRESH=1 uv run python -m eval.condensed_pipeline      # regenerate discriminators + regrade
  LISTWISE_N=30 uv run python -m eval.condensed_pipeline
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
from eval.function_property_rerank import _clause_key
from eval.ground_discriminator_rerank import Grade, _decompose_prompt, _grade_prompt
from eval.harness import recall_at_k
from eval.listwise_rerank import Ranking, _listwise_prompt
from rag_wright.contracts.identifiers import ChunkId
from rag_wright.models.profiles import profile_for
from rag_wright.models.seam import build_model
from rag_wright.spans.property_extractor import SeamPropertyExtractor
from rag_wright.store.arcadedb import SPAN_TYPE, ArcadeDBStore, _sql_str, _str_array
from rag_wright.util.concurrent import map_concurrent

DB = os.environ.get("PIVOT_DB", "ragwright_acord_pivot")
MODEL = os.environ.get("RERANK_MODEL", "google/gemma-4-31b-it")
DECOMP_MODEL = os.environ.get("DECOMP_MODEL", "google/gemma-4-31b-it")
N = int(os.environ.get("LISTWISE_N", "20"))
DISC_CACHE = Path("data/models/condensed_disc.json")
SCORE_CACHE = Path("data/models/condensed_scores.jsonl")
ORDER_CACHE = Path("data/models/condensed_orders.jsonl")
QCON_CACHE = Path("data/models/condensed_qconstraints.json")
PROGRESS = Path("data/models/condensed_progress.log")
CONTRA = ("favorable", "exception", "carve", "waiv", "affiliate", "first party", "third party",
          "mutual", "different", "seller", "buyer", "non-reliance", "unilateral")


def _progress(msg: str) -> None:
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS.write_text(msg + "\n", encoding="utf-8")
    print(msg, flush=True)


def _runnable(schema, timeout):
    prof = profile_for(MODEL)
    skw = {"method": prof.structured_method}
    if prof.structured_extra_body is not None:
        skw["extra_body"] = prof.structured_extra_body
    return build_model(MODEL, timeout=timeout, max_retries=0).with_structured_output(schema, **skw)


def main() -> None:
    load_dotenv()
    if os.environ.get("FRESH") == "1":
        for p in (DISC_CACHE, SCORE_CACHE, ORDER_CACHE):
            p.unlink(missing_ok=True)
    store = ArcadeDBStore.from_env(database=DB, reset=False)
    corpus = {c.clause_id: c.text for c in load_corpus()}
    queries = load_test_queries()

    def oracle_f1(gold):
        reach: dict[str, int] = {}
        for g in gold:
            for f in {r["function"] for r in store._query(
                    f"SELECT function FROM {SPAN_TYPE} WHERE parent_okf_path = {_sql_str(g)}")}:
                reach[f] = reach.get(f, 0) + 1
        return max(reach, key=reach.get) if reach else None

    def pool_for(fn):
        return [] if not fn else list(dict.fromkeys(r["parent_okf_path"] for r in store._query(
            f"SELECT parent_okf_path FROM {SPAN_TYPE} WHERE function IN {_str_array([fn])}")))

    fn_of = {q.query_id: oracle_f1(sorted(q.relevant)) for q in queries}
    pool_of = {q.query_id: pool_for(fn_of[q.query_id]) for q in queries}
    judged_of = {q.query_id: [c for c in pool_of[q.query_id] if c in q.graded] for q in queries}

    # 1) discriminators -- PERSISTED (deterministic across runs)
    DISC_CACHE.parent.mkdir(parents=True, exist_ok=True)
    disc_of = json.loads(DISC_CACHE.read_text()) if DISC_CACHE.exists() else {}
    todo = [q for q in queries if q.query_id not in disc_of and judged_of[q.query_id]]
    if todo:
        dec = build_model(DECOMP_MODEL, temperature=0.0)
        _progress(f"[decompose] {len(todo)} queries via {DECOMP_MODEL}")
        out = map_concurrent(todo, lambda q: dec.invoke(_decompose_prompt(q.text)).content.strip(),
                             max_concurrency=8, label="[decompose]", echo=True)
        disc_of.update({q.query_id: d for q, d in zip(todo, out)})
        DISC_CACHE.write_text(json.dumps(disc_of, indent=0))

    # 2) pointwise grade of judged clauses -- cached by (query_id, clause)
    grunner = _runnable(Grade, 25.0)

    def _score(disc, clause):
        # returns None on total failure -> caller does NOT cache it (so a re-run retries, never
        # persisting a transient-error fallback as a real 0.0 grade)
        for _ in range(3):
            try:
                v = grunner.invoke(_grade_prompt(disc, clause))
            except Exception:  # noqa: BLE001
                continue
            if v is not None:
                return max(0.0, min(1.0, float(v.score)))
        return None

    lock = threading.Lock()
    scores: dict[tuple[str, str], float] = {}
    if SCORE_CACHE.exists():
        for line in SCORE_CACHE.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                scores[(r["qid"], r["clause"])] = r["score"]
    jobs = [(q.query_id, c) for q in queries for c in judged_of[q.query_id]]

    def _grade_job(job):
        qid, c = job
        if (qid, c) in scores:
            return scores[(qid, c)]
        s = _score(disc_of[qid], corpus.get(c, ""))
        if s is None:  # transient failure -> use 0.0 for this pass, but do NOT cache (retry next run)
            return 0.0
        with lock:
            with SCORE_CACHE.open("a") as f:
                f.write(json.dumps({"qid": qid, "clause": c, "score": s}) + "\n")
                f.flush()
            scores[(qid, c)] = s
        return s

    _progress(f"[pointwise] {len(jobs)} judged grades via {MODEL}")
    for job, s in zip(jobs, map_concurrent(jobs, _grade_job, max_concurrency=8,
                                           progress_path=PROGRESS, label="[pointwise]", echo=True, every=25)):
        scores[job] = s

    def pointwise_order(qid):
        return sorted(judged_of[qid], key=lambda c: (-scores.get((qid, c), 0.0), c))

    # 3) listwise re-order of the pointwise top-N
    lrunner = _runnable(Ranking, 40.0)
    orders: dict[str, list[int]] = {}
    if ORDER_CACHE.exists():
        for line in ORDER_CACHE.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                orders[r["qid"]] = r["order"]

    def _listwise(qid):
        if qid in orders:
            return orders[qid]
        win = pointwise_order(qid)[:N]
        if len(win) <= 1:
            return list(range(1, len(win) + 1))
        order: list[int] = []
        for _ in range(3):
            try:
                v = lrunner.invoke(_listwise_prompt(disc_of[qid], [corpus.get(c, "") for c in win]))
            except Exception:  # noqa: BLE001
                continue
            if v is not None:
                order = v.order
                break
        if not order:  # all retries failed -> do NOT cache; retry next run
            return order
        with lock:
            with ORDER_CACHE.open("a") as f:
                f.write(json.dumps({"qid": qid, "order": order}) + "\n")
                f.flush()
            orders[qid] = order
        return order

    ltodo = [q.query_id for q in queries if len(judged_of[q.query_id]) > 1]
    _progress(f"[listwise] {len(ltodo)} queries, top-{N} re-order via {MODEL}")
    for qid, o in zip(ltodo, map_concurrent(ltodo, _listwise, max_concurrency=8,
                                            progress_path=PROGRESS, label="[listwise]", echo=True, every=5)):
        orders[qid] = o

    def listwise_order(qid):
        win = pointwise_order(qid)[:N]
        seen: list[str] = []
        for i in orders.get(qid, []):
            if 1 <= i <= len(win) and win[i - 1] not in seen:
                seen.append(win[i - 1])
        for c in win:
            if c not in seen:
                seen.append(c)
        return seen + [c for c in pointwise_order(qid) if c not in set(win)]

    def tiebreak_order(qid):
        # keep the confident pointwise order; use listwise position ONLY to break equal-score ties
        win = pointwise_order(qid)[:N]
        lw_rank = {win[i - 1]: r for r, i in enumerate(orders.get(qid, [])) if 1 <= i <= len(win)}
        ranked = sorted(judged_of[qid],
                        key=lambda c: (-scores.get((qid, c), 0.0), lw_rank.get(c, N), c))
        return ranked

    # 3.5) DETERMINISTIC property tie-break: decompose query -> property constraints (LLM, cached), count
    # each clause's matching graph property-values, use the count ONLY to break equal-score ties (free, safe).
    qcon = {k: {tuple(x) for x in v} for k, v in json.loads(QCON_CACHE.read_text()).items()} \
        if QCON_CACHE.exists() else {}
    qtodo = [q for q in queries if q.query_id not in qcon and judged_of[q.query_id]
             and fn_of[q.query_id] and fn_of[q.query_id] != "NONE"]
    if qtodo:
        extractor = SeamPropertyExtractor(model_id=DECOMP_MODEL)

        def _qcon(q):
            rec = extractor(chunk_id=ChunkId.of("query", 0, q.text), function=fn_of[q.query_id],
                            text=q.text, span_id="")
            return {(a.dimension.value, a.value) for a in rec.assertions}

        _progress(f"[qconstraints] {len(qtodo)} queries via {DECOMP_MODEL}")
        for q, cc in zip(qtodo, map_concurrent(qtodo, _qcon, max_concurrency=8,
                                               label="[qconstraints]", echo=True)):
            qcon[q.query_id] = cc
        QCON_CACHE.write_text(json.dumps({k: [list(t) for t in v] for k, v in qcon.items()}, indent=0))

    clause_props: dict[str, set[tuple[str, str]]] = {}
    for q in queries:
        for c in judged_of[q.query_id]:
            if c not in clause_props:
                clause_props[c] = {(r["dimension"], r["value"])
                                   for r in store.clause_property_values(_clause_key(c, corpus.get(c, "")))}

    def property_tiebreak_order(qid):
        qc = qcon.get(qid, set())
        ranked = sorted(judged_of[qid],
                        key=lambda c: (-scores.get((qid, c), 0.0), -len(qc & clause_props.get(c, set())), c))
        return ranked

    # 4) metrics
    def agg(order_of):
        r10 = [recall_at_k(order_of(q.query_id), q.relevant, 10) for q in queries if judged_of[q.query_id]]
        r20 = [recall_at_k(order_of(q.query_id), q.relevant, 20) for q in queries if judged_of[q.query_id]]
        nd = [ndcg_at_k(order_of(q.query_id), q.graded, 10) for q in queries if judged_of[q.query_id]]
        return statistics.mean(r10), statistics.mean(r20), statistics.mean(nd)

    p = agg(pointwise_order)
    lw = agg(listwise_order)
    nq = sum(1 for q in queries if judged_of[q.query_id])
    print(f"\n=== CONDENSED pipeline | queries={nq} | model={MODEL} | listwise top-{N} ===", flush=True)
    print("                recall@10   recall@20   nDCG@10", flush=True)
    print(f"pointwise       {p[0]:.3f}       {p[1]:.3f}       {p[2]:.3f}", flush=True)
    print(f"+ listwise      {lw[0]:.3f}       {lw[1]:.3f}       {lw[2]:.3f}   "
          f"(dr@10={lw[0] - p[0]:+.3f}  dnDCG={lw[2] - p[2]:+.3f})", flush=True)
    tb = agg(tiebreak_order)
    print(f"+ lw-tiebreak   {tb[0]:.3f}       {tb[1]:.3f}       {tb[2]:.3f}   "
          f"(dr@10={tb[0] - p[0]:+.3f}  dnDCG={tb[2] - p[2]:+.3f})", flush=True)
    pt = agg(property_tiebreak_order)
    print(f"+ prop-tiebreak {pt[0]:.3f}       {pt[1]:.3f}       {pt[2]:.3f}   "
          f"(dr@10={pt[0] - p[0]:+.3f}  dnDCG={pt[2] - p[2]:+.3f})   [deterministic, free]", flush=True)

    movers = []
    for q in queries:
        if not judged_of[q.query_id]:
            continue
        pn = ndcg_at_k(pointwise_order(q.query_id), q.graded, 10)
        ln = ndcg_at_k(listwise_order(q.query_id), q.graded, 10)
        movers.append((ln - pn, q.text, any(k in q.text.lower() for k in CONTRA)))
    up = [m for m in movers if m[0] > 0.02]
    dn = [m for m in movers if m[0] < -0.02]
    print(f"\nper-query nDCG@10: up={len(up)}  flat={len(movers) - len(up) - len(dn)}  down={len(dn)}", flush=True)
    print("  biggest improvements:", flush=True)
    for d, txt, c in sorted(movers, reverse=True)[:6]:
        print(f"    {d:+.3f} {'CONTRA' if c else 'plain '} {txt[:52]}", flush=True)
    print("  biggest regressions:", flush=True)
    for d, txt, c in sorted(movers)[:6]:
        print(f"    {d:+.3f} {'CONTRA' if c else 'plain '} {txt[:52]}", flush=True)
    store.close()


if __name__ == "__main__":
    main()
