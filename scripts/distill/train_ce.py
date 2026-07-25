"""P1 core: train a CrossEncoder on ACORD graded pairs (one query-disjoint fold held out) and predict the
held-out pairs. Pure function `train_and_predict` so it runs identically in a local smoke test and inside the
Modal GPU job. Negatives (grade 0 = 97% of pairs) are downsampled to `neg_ratio`x the positives, strided so
the sample spreads across queries. Label = grade/4 (soft relevance), BinaryCrossEntropy loss.

  SMOKE=1 uv run python -m scripts.distill.train_ce   # tiny local validation (1 fold, small subset, MiniLM)
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def train_and_predict(rows, held_fold, base_model, *, neg_ratio=8, epochs=2, bs=32,
                      lr=2e-5, max_len=512, out_dir="/tmp/ce_out", fp16=True):
    from datasets import Dataset
    from sentence_transformers import CrossEncoder
    from sentence_transformers.cross_encoder import CrossEncoderTrainer, CrossEncoderTrainingArguments
    from sentence_transformers.cross_encoder.losses import BinaryCrossEntropyLoss

    train = [r for r in rows if r["fold"] != held_fold]
    test = [r for r in rows if r["fold"] == held_fold]
    pos = [r for r in train if r["grade"] >= 1]
    neg = [r for r in train if r["grade"] == 0]
    keep = max(1, len(neg) // max(1, len(pos) * neg_ratio))
    tr = pos + neg[::keep]  # strided negative sample (deterministic, spread across queries)

    ds = Dataset.from_dict({
        "query": [r["query"] for r in tr],
        "passage": [r["text"] for r in tr],
        "label": [r["grade"] / 4.0 for r in tr],
    })
    model = CrossEncoder(base_model, num_labels=1, max_length=max_len)
    args = CrossEncoderTrainingArguments(
        output_dir=out_dir, num_train_epochs=epochs, per_device_train_batch_size=bs,
        learning_rate=lr, warmup_ratio=0.1, fp16=fp16, bf16=False, report_to=[],
        logging_steps=50, save_strategy="no", dataloader_num_workers=0,
    )
    trainer = CrossEncoderTrainer(model=model, args=args, train_dataset=ds, loss=BinaryCrossEntropyLoss(model))
    trainer.train()

    scores = model.predict([(r["query"], r["text"]) for r in test], batch_size=128, show_progress_bar=False)
    preds = [{"query_id": r["query_id"], "clause_id": r["clause_id"], "score": float(s)}
             for r, s in zip(test, scores)]
    return preds, model


def _smoke():
    rows = [json.loads(x) for x in Path("data/models/ce/dataset.jsonl").read_text().splitlines() if x.strip()]
    # tiny: hold fold 0, train on folds 1-2 only, small neg ratio, 1 epoch
    sub = [r for r in rows if r["fold"] in (0, 1, 2)]
    preds, _ = train_and_predict(sub, held_fold=0, base_model="cross-encoder/ms-marco-MiniLM-L6-v2",
                                 neg_ratio=4, epochs=1, bs=16, fp16=False)
    # quick condensed nDCG@10 on the held fold using the exported grades
    from collections import defaultdict
    from eval.acord_retrieval import ndcg_at_k
    grade = {(r["query_id"], r["clause_id"]): r["grade"] for r in sub}
    byq = defaultdict(list)
    for p in preds:
        byq[p["query_id"]].append((p["clause_id"], p["score"]))
    import statistics
    nd = []
    for qid, lst in byq.items():
        order = [c for c, _ in sorted(lst, key=lambda cs: -cs[1])]
        graded = {c: grade[(qid, c)] for c, _ in lst}
        nd.append(ndcg_at_k(order, graded, 10))
    print(f"[smoke] fold0 held, {len(preds)} preds over {len(byq)} queries -> condensed nDCG@10={statistics.mean(nd):.3f}",
          flush=True)


if __name__ == "__main__":
    if os.environ.get("SMOKE") == "1":
        _smoke()
