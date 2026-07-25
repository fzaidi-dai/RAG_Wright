"""P1 export: ACORD graded (query, clause, grade) pairs -> a self-contained dataset for cross-encoder
training on Modal. Query-disjoint 5-fold assignment (so a query's pairs never straddle train/test). Ships
the clause + query TEXT inline (the Modal job has no corpus loader). Grade dist is ~97% grade-0, so the
trainer downsamples negatives; the export keeps everything and records the fold.

  uv run python -m scripts.distill.export_ce_dataset
"""
from __future__ import annotations

import json
from pathlib import Path

from eval.acord import load_corpus, load_test_queries

OUT = Path("data/models/ce")
NFOLDS = 5


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    corpus = {c.clause_id: c.text for c in load_corpus()}
    queries = load_test_queries()
    # deterministic query-disjoint folds: sort query_ids, assign index % NFOLDS
    qids = sorted(q.query_id for q in queries)
    fold_of = {qid: i % NFOLDS for i, qid in enumerate(qids)}

    n, pos = 0, 0
    with (OUT / "dataset.jsonl").open("w", encoding="utf-8") as f:
        for q in queries:
            for cid, grade in q.graded.items():
                text = " ".join(corpus.get(cid, "").split())[:2000]
                if not text:
                    continue
                f.write(json.dumps({
                    "query_id": q.query_id, "query": q.text, "clause_id": cid,
                    "text": text, "grade": int(grade), "fold": fold_of[q.query_id],
                }) + "\n")
                n += 1
                pos += grade >= 2
    (OUT / "folds.json").write_text(json.dumps(fold_of, indent=0))
    print(f"wrote {n} pairs ({pos} gold>=2) over {len(queries)} queries, {NFOLDS} query-disjoint folds -> {OUT}/dataset.jsonl")
    from collections import Counter
    print("fold sizes (queries):", dict(sorted(Counter(fold_of.values()).items())))


if __name__ == "__main__":
    main()
