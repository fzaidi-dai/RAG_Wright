"""P1/P1.5 export: ACORD graded (query, clause, grade) pairs -> a self-contained dataset for cross-encoder
training on Modal. Adds `in_pool`: whether the clause is in the query's ORACLE FUNCTION pool -- the flag
P1.5 uses for HARD-NEGATIVE MINING (a grade-0 clause in-pool is a same-function distractor, the eval-time
hard negative). Query-disjoint 5-fold. Ships the clause + query TEXT inline (Modal has no corpus/store).

  uv run --no-sync python -m scripts.distill.export_ce_dataset
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

from eval.acord import load_corpus, load_test_queries
from rag_wright.store.arcadedb import SPAN_TYPE, ArcadeDBStore, _sql_str

OUT = Path("data/models/ce")
NFOLDS = 5


def main() -> None:
    load_dotenv()
    OUT.mkdir(parents=True, exist_ok=True)
    store = ArcadeDBStore.from_env(database="ragwright_acord_pivot", reset=False)
    corpus = {c.clause_id: c.text for c in load_corpus()}
    queries = load_test_queries()

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

    pool_of = {q.query_id: poolset(of1(sorted(q.relevant))) for q in queries}
    qids = sorted(q.query_id for q in queries)
    fold_of = {qid: i % NFOLDS for i, qid in enumerate(qids)}

    n, pos, hard = 0, 0, 0
    with (OUT / "dataset.jsonl").open("w", encoding="utf-8") as f:
        for q in queries:
            pool = pool_of[q.query_id]
            for cid, grade in q.graded.items():
                text = " ".join(corpus.get(cid, "").split())[:2000]
                if not text:
                    continue
                in_pool = cid in pool
                f.write(json.dumps({
                    "query_id": q.query_id, "query": q.text, "clause_id": cid, "text": text,
                    "grade": int(grade), "fold": fold_of[q.query_id], "in_pool": in_pool,
                }) + "\n")
                n += 1
                pos += grade >= 2
                hard += grade == 0 and in_pool
    (OUT / "folds.json").write_text(json.dumps(fold_of, indent=0))
    print(f"wrote {n} pairs ({pos} gold>=2, {hard} in-pool grade-0 HARD negatives) over {len(queries)} queries -> {OUT}/dataset.jsonl")
    print("fold sizes (queries):", dict(sorted(Counter(fold_of.values()).items())))
    store.close()


if __name__ == "__main__":
    main()
