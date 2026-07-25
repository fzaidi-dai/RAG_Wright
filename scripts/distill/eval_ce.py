"""P1 eval: rank each query's oracle-function-pool judged clauses by the trained CrossEncoder's held-out
score, condensed nDCG@10 / recall@10/@20, vs BGE-alone and the Gemma pointwise reference. Apples-to-apples
with the prior condensed numbers (BGE 0.526 / Gemma 0.706 over oracle-pool judged).

  PREDS=data/models/ce/preds_minilm_smoke.jsonl uv run --no-sync python -m scripts.distill.eval_ce
"""
from __future__ import annotations

import json
import os
import statistics
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

from eval.acord import load_corpus, load_test_queries
from eval.acord_retrieval import ndcg_at_k
from eval.harness import recall_at_k
from rag_wright.capabilities.reranking import BGEReranker
from rag_wright.store.arcadedb import SPAN_TYPE, ArcadeDBStore, _sql_str

CONTRA = {
    "seller-favorable cap on liability clauses", "mutual liability cap", "unilateral liability cap",
    "buyer-favorable warranty disclaimer clauses", "seller-favorable indemnification clauses",
    "mutual indemnification provisions", "unilateral indemnification clause", "mutual indirect damages waiver",
    "unilateral indirect damages waiver", "buyer-favorable waiver of indirect damages clauses",
}


def main() -> None:
    load_dotenv()
    preds_path = Path(os.environ.get("PREDS", "data/models/ce/preds_minilm.jsonl"))
    store = ArcadeDBStore.from_env(database="ragwright_acord_pivot", reset=False)
    corpus = {c.clause_id: c.text for c in load_corpus()}
    qmap = {q.query_id: q for q in load_test_queries()}

    ce = defaultdict(dict)  # query_id -> {clause_id: score}
    for line in preds_path.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            ce[r["query_id"]][r["clause_id"]] = r["score"]

    gp = {}
    csc = Path("data/models/condensed_scores.jsonl")
    if csc.exists():
        for line in csc.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                gp[(r["qid"], r["clause"])] = r["score"]

    def of1(gold):
        rr = {}
        for g in gold:
            for f in {x["function"] for x in store._query(
                    f"SELECT function FROM {SPAN_TYPE} WHERE parent_okf_path = {_sql_str(g)}")}:
                rr[f] = rr.get(f, 0) + 1
        return max(rr, key=rr.get) if rr else None

    def poolset(fn):
        return {x["parent_okf_path"] for x in store._query(
            f"SELECT parent_okf_path FROM {SPAN_TYPE} WHERE function = {_sql_str(fn)}")}

    bge = BGEReranker()
    m = {"ce": defaultdict(list), "bge": defaultdict(list), "gemma": defaultdict(list)}

    def add(order, q, tag):
        m[tag]["nd"].append(ndcg_at_k(order, q.graded, 10))
        m[tag]["r10"].append(recall_at_k(order, q.relevant, 10))
        m[tag]["r20"].append(recall_at_k(order, q.relevant, 20))

    evaluated = []
    for qid, scores in ce.items():
        q = qmap[qid]
        pool = poolset(of1(sorted(q.relevant)))
        judged = [c for c in q.graded if c in pool and c in scores]
        if not judged:
            continue
        evaluated.append(q.text)
        ce_order = sorted(judged, key=lambda c: -scores[c])
        bge_scores = dict(zip(judged, bge.score(q.text, [corpus[c] for c in judged])))
        bge_order = sorted(judged, key=lambda c: -bge_scores[c])
        gem_order = sorted(judged, key=lambda c: -gp.get((qid, c), 0.0))
        add(ce_order, q, "ce")
        add(bge_order, q, "bge")
        add(gem_order, q, "gemma")

    def report(label, subset=None):
        idx = [i for i, t in enumerate(evaluated) if subset is None or t in subset]
        if not idx:
            return
        print(f"\n{label}  (n={len(idx)})", flush=True)
        print(f"  {'method':<8}{'nDCG@10':<10}{'recall@10':<11}{'recall@20':<10}", flush=True)
        for tag in ("bge", "ce", "gemma"):
            nd = statistics.mean(m[tag]["nd"][i] for i in idx)
            r10 = statistics.mean(m[tag]["r10"][i] for i in idx)
            r20 = statistics.mean(m[tag]["r20"][i] for i in idx)
            print(f"  {tag:<8}{nd:<10.3f}{r10:<11.3f}{r20:<10.3f}", flush=True)

    print(f"=== CE eval | preds={preds_path.name} | condensed, oracle-pool judged ===", flush=True)
    report("ALL evaluated queries")
    report("CONTRASTIVE subset", CONTRA)
    store.close()


if __name__ == "__main__":
    main()
