"""Fast combined CE eval: cache oracle-pool judged sets + BGE scores ONCE, then rank every preds file's
held-out CE scores. Condensed nDCG@10 / recall@10/@20 vs BGE and the Gemma reference, all-57 + contrastive.

  HF_HUB_OFFLINE=1 uv run --no-sync python -m scripts.distill.eval_all preds_legalbert_hard preds_legalbert_hardfeat preds_legalbert_distillfeat
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

from eval.acord import load_corpus, load_test_queries
from eval.acord_retrieval import ndcg_at_k
from eval.harness import recall_at_k
from rag_wright.capabilities.reranking import BGEReranker
from rag_wright.store.arcadedb import SPAN_TYPE, ArcadeDBStore, _sql_str

OUT = Path("data/models/ce")
POOLS = OUT / "pools.json"
BGEC = OUT / "bge_scores.jsonl"
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

    # pool_judged per query (cached; the slow oracle-function + pool lookups happen once)
    if POOLS.exists():
        pools = json.loads(POOLS.read_text())
    else:
        store = ArcadeDBStore.from_env(database="ragwright_acord_pivot", reset=False)

        def of1(gold):
            rr = {}
            for g in gold:
                for f in {x["function"] for x in store._query(
                        f"SELECT function FROM {SPAN_TYPE} WHERE parent_okf_path = {_sql_str(g)}")}:
                    rr[f] = rr.get(f, 0) + 1
            return max(rr, key=rr.get) if rr else None

        pools = {}
        for q in qmap.values():
            fn = of1(sorted(q.relevant))
            pool = {x["parent_okf_path"] for x in store._query(
                f"SELECT parent_okf_path FROM {SPAN_TYPE} WHERE primary_tag = {_sql_str(fn)}")} if fn else set()
            pools[q.query_id] = [c for c in q.graded if c in pool]
        POOLS.write_text(json.dumps(pools))
        store.close()
        print(f"[cache] built pools -> {POOLS}", flush=True)

    # BGE per (query, pool-judged clause) (cached once)
    bge_score = {}
    if BGEC.exists():
        for line in BGEC.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                bge_score[(r["qid"], r["clause"])] = r["bge"]
    else:
        bge = BGEReranker()
        with BGEC.open("w", encoding="utf-8") as f:
            for qid, cs in pools.items():
                if not cs:
                    continue
                for c, s in zip(cs, bge.score(qmap[qid].text, [corpus[c] for c in cs])):
                    bge_score[(qid, c)] = float(s)
                    f.write(json.dumps({"qid": qid, "clause": c, "bge": float(s)}) + "\n")
        print(f"[cache] built BGE scores -> {BGEC}", flush=True)

    gp = {}
    for line in (OUT.parent / "condensed_scores.jsonl").read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            gp[(r["qid"], r["clause"])] = r["score"]

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
        "bge": metrics(lambda qid, cs: sorted(cs, key=lambda c: -bge_score.get((qid, c), 0.0))),
        "gemma": metrics(lambda qid, cs: sorted(cs, key=lambda c: -gp.get((qid, c), 0.0))),
    }
    for name in sys.argv[1:]:
        ce = defaultdict(dict)
        for line in (OUT / f"{name}.jsonl").read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                ce[r["query_id"]][r["clause_id"]] = r["score"]
        methods[name] = metrics(lambda qid, cs, ce=ce: sorted(cs, key=lambda c: -ce[qid].get(c, 0.0)))

    for label, subset in (("ALL 57", None), ("CONTRASTIVE (10)", CONTRA)):
        print(f"\n=== {label} ===", flush=True)
        print(f"  {'method':<28}{'nDCG@10':<10}{'recall@10':<11}{'recall@20':<10}", flush=True)
        for name, rows in methods.items():
            print(f"  {name:<28}{mean(rows, 0, subset):<10.3f}{mean(rows, 1, subset):<11.3f}"
                  f"{mean(rows, 2, subset):<10.3f}", flush=True)


if __name__ == "__main__":
    main()
