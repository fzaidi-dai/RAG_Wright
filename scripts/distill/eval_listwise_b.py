"""Measure (b): function -> first-stage CE (a) -> ONE Gemma listwise call over the top-K -> reorder.
Condensed nDCG@10 / recall@10/@20 vs (a) CE, BGE, and pointwise-Gemma. Reuses pools.json, bge_scores.jsonl,
condensed_scores (Gemma pointwise), preds_legalbert_hard (first stage), condensed_disc.json (discriminators),
and Ranking/_listwise_prompt from eval.listwise_rerank. One call/query, crash-safe cache.

  HF_HUB_OFFLINE=1 uv run --no-sync python -m scripts.distill.eval_listwise_b
"""
from __future__ import annotations

import json
import statistics
import threading
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

from eval.acord import load_corpus, load_test_queries
from eval.acord_retrieval import ndcg_at_k
from eval.harness import recall_at_k
from eval.listwise_rerank import Ranking, _listwise_prompt
from rag_wright.models.profiles import profile_for
from rag_wright.models.seam import build_model

OUT = Path("data/models/ce")
K = 40
ORDERS = OUT / "listwise_b_orders.jsonl"
MODEL = "google/gemma-4-31b-it"
CONTRA = {
    "seller-favorable cap on liability clauses", "mutual liability cap", "unilateral liability cap",
    "buyer-favorable warranty disclaimer clauses", "seller-favorable indemnification clauses",
    "mutual indemnification provisions", "unilateral indemnification clause", "mutual indirect damages waiver",
    "unilateral indirect damages waiver", "buyer-favorable waiver of indirect damages clauses",
}


def main() -> None:
    load_dotenv()
    corpus = {c.clause_id: c.text for c in load_corpus()}
    qmap = {q.query_id: q for q in load_test_queries()}
    pools = json.loads((OUT / "pools.json").read_text())
    disc_of = json.loads((OUT / "condensed_disc.json").read_text()) if (OUT / "condensed_disc.json").exists() \
        else json.loads(Path("data/models/condensed_disc.json").read_text())

    def load_scores(path, key_clause):
        d = defaultdict(dict)
        for line in Path(path).read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                d[r["qid"] if "qid" in r else r["query_id"]][r[key_clause]] = r["score"] if "score" in r else r["bge"]
        return d

    ce_a = defaultdict(dict)
    for line in (OUT / "preds_legalbert_hard.jsonl").read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            ce_a[r["query_id"]][r["clause_id"]] = r["score"]
    bge = defaultdict(dict)
    for line in (OUT / "bge_scores.jsonl").read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            bge[r["qid"]][r["clause"]] = r["bge"]
    gp = defaultdict(dict)
    for line in Path("data/models/condensed_scores.jsonl").read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            gp[r["qid"]][r["clause"]] = r["score"]

    prof = profile_for(MODEL)
    skw = {"method": prof.structured_method}
    if prof.structured_extra_body is not None:
        skw["extra_body"] = prof.structured_extra_body
    runner = build_model(MODEL, timeout=40.0, max_retries=1).with_structured_output(Ranking, **skw)

    done, lock = {}, threading.Lock()
    if ORDERS.exists():
        for line in ORDERS.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                done[r["qid"]] = r["order"]

    def listwise_final(qid):
        pool = pools[qid]
        first = sorted(pool, key=lambda c: -ce_a[qid].get(c, 0.0))  # first stage = (a) CE
        topk = first[:K]
        if len(topk) <= 1:
            return first
        if qid in done:
            order = done[qid]
        else:
            test = disc_of.get(qid, qmap[qid].text)
            order = []
            for _ in range(3):
                try:
                    v = runner.invoke(_listwise_prompt(test, [corpus.get(c, "") for c in topk]))
                except Exception:  # noqa: BLE001
                    continue
                if v is not None:
                    order = v.order
                    break
            with lock:
                if order:
                    with ORDERS.open("a", encoding="utf-8") as f:
                        f.write(json.dumps({"qid": qid, "order": order}) + "\n")
                        f.flush()
                    done[qid] = order
        seen = []
        for i in order:
            if 1 <= i <= len(topk) and topk[i - 1] not in seen:
                seen.append(topk[i - 1])
        for c in topk:
            if c not in seen:
                seen.append(c)
        return seen + [c for c in first if c not in set(topk)]

    # run listwise for all queries (concurrent)
    from rag_wright.util.concurrent import map_concurrent
    qids = [qid for qid in pools if pools[qid]]
    print(f"[listwise-b] {len(qids)} queries, top-{K}, one {MODEL} call each", flush=True)
    finals = dict(zip(qids, map_concurrent(qids, listwise_final, max_concurrency=8, label="[listwise-b]", echo=True, every=5)))

    def metrics(order_of):
        rows = {}
        for q in qmap.values():
            cs = pools[q.query_id]
            if not cs:
                continue
            order = order_of(q.query_id, cs)
            rows[q.text] = (ndcg_at_k(order, q.graded, 10),
                            recall_at_k(order, q.relevant, 10), recall_at_k(order, q.relevant, 20))
        return rows

    def mean(rows, i, subset=None):
        vals = [v[i] for t, v in rows.items() if subset is None or t in subset]
        return statistics.mean(vals) if vals else 0.0

    methods = {
        "bge": metrics(lambda qid, cs: sorted(cs, key=lambda c: -bge[qid].get(c, 0.0))),
        "(a) CE hard": metrics(lambda qid, cs: sorted(cs, key=lambda c: -ce_a[qid].get(c, 0.0))),
        "(b) listwise": metrics(lambda qid, cs: finals[qid]),
        "gemma pointwise": metrics(lambda qid, cs: sorted(cs, key=lambda c: -gp[qid].get(c, 0.0))),
    }
    for label, subset in (("ALL 57", None), ("CONTRASTIVE (10)", CONTRA)):
        print(f"\n=== {label} ===", flush=True)
        print(f"  {'method':<18}{'nDCG@10':<10}{'recall@10':<11}{'recall@20':<10}", flush=True)
        for name, rows in methods.items():
            print(f"  {name:<18}{mean(rows, 0, subset):<10.3f}{mean(rows, 1, subset):<11.3f}"
                  f"{mean(rows, 2, subset):<10.3f}", flush=True)


if __name__ == "__main__":
    main()
