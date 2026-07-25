"""P1 on Modal: fine-tune a CrossEncoder on ACORD graded pairs, one query-disjoint fold per GPU task,
collect held-out predictions for every query. Grounded against the modal SDK in the framework graph
(App / Image.pip_install / function(gpu=) / Volume.batch_upload / Function.starmap / local_entrypoint).

  modal run scripts/distill/train_modal.py --model minilm --smoke   # 1 fold, 1 epoch (cheap validation)
  modal run scripts/distill/train_modal.py --model minilm           # full 5-fold CV
  modal run scripts/distill/train_modal.py --model legalbert
"""
from __future__ import annotations

import json
from pathlib import Path

import modal

app = modal.App("ce-distill")
image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("sentence-transformers==5.6.0", "torch", "datasets", "accelerate", "transformers")
)
vol = modal.Volume.from_name("ce-distill", create_if_missing=True)
DATA = "/data"
BASES = {"minilm": "cross-encoder/ms-marco-MiniLM-L6-v2", "legalbert": "nlpaueb/legal-bert-base-uncased"}


def train_and_predict(rows, held_fold, base_model, *, easy_per_hard=1, epochs=2, bs=32, lr=2e-5,
                      max_len=512, feat_of=None, teacher=None):
    from datasets import Dataset
    from sentence_transformers import CrossEncoder
    from sentence_transformers.cross_encoder import CrossEncoderTrainer, CrossEncoderTrainingArguments
    from sentence_transformers.cross_encoder.losses import BinaryCrossEntropyLoss

    def passage(r):  # P2: prepend the KG structural-feature string so the encoder attends to it
        if feat_of is None:
            return r["text"]
        return f"[STRUCT] {feat_of.get(r['clause_id'], '')} [/STRUCT] {r['text']}"

    train = [r for r in rows if r["fold"] != held_fold]
    test = [r for r in rows if r["fold"] == held_fold]
    if teacher is not None:
        # P3 DISTILLATION: NO gold grades. Label = the Gemma teacher's score (what we'd have on an ungraded
        # corpus); candidates = the function-pool (in_pool) clauses + a matched easy sample (label 0).
        inpool = [r for r in train if r.get("in_pool")]
        easy = [r for r in train if not r.get("in_pool")]
        n_easy = len(inpool) * easy_per_hard
        easy_s = easy[:: max(1, len(easy) // max(1, n_easy))] if n_easy else []
        tr = inpool + easy_s
        labels = [teacher.get((r["query_id"], r["clause_id"]), 0.0) for r in tr]
    else:
        # HARD-NEGATIVE MINING (P1.5): positives (grade>=1, soft label) + ALL in-pool grade-0 hard negatives
        # (the same-function eval distractors) + a matched sample of easy (out-of-pool) negatives for breadth.
        pos = [r for r in train if r["grade"] >= 1]
        hard = [r for r in train if r["grade"] == 0 and r.get("in_pool")]
        easy = [r for r in train if r["grade"] == 0 and not r.get("in_pool")]
        n_easy = len(hard) * easy_per_hard
        easy_s = easy[:: max(1, len(easy) // max(1, n_easy))] if n_easy else []
        tr = pos + hard + easy_s
        labels = [r["grade"] / 4.0 for r in tr]

    ds = Dataset.from_dict({
        "query": [r["query"] for r in tr],
        "passage": [passage(r) for r in tr],
        "label": labels,
    })
    model = CrossEncoder(base_model, num_labels=1, max_length=max_len)
    args = CrossEncoderTrainingArguments(
        output_dir="/tmp/out", num_train_epochs=epochs, per_device_train_batch_size=bs,
        learning_rate=lr, warmup_ratio=0.1, fp16=True, report_to=[], logging_steps=50,
        save_strategy="no", dataloader_num_workers=2,
    )
    CrossEncoderTrainer(model=model, args=args, train_dataset=ds, loss=BinaryCrossEntropyLoss(model)).train()
    scores = model.predict([(r["query"], passage(r)) for r in test], batch_size=256, show_progress_bar=False)
    return [{"query_id": r["query_id"], "clause_id": r["clause_id"], "grade": r["grade"], "score": float(s)}
            for r, s in zip(test, scores)]


@app.function(image=image, gpu="A10", volumes={DATA: vol}, timeout=3600)
def train_fold(fold: int, base_model: str, easy_per_hard: int, epochs: int, bs: int,
               use_features: bool, distill: bool):
    rows = [json.loads(x) for x in open(f"{DATA}/dataset.jsonl", encoding="utf-8") if x.strip()]
    feat_of = None
    if use_features:
        feat_of = {}
        for x in open(f"{DATA}/clause_features.jsonl", encoding="utf-8"):
            if x.strip():
                r = json.loads(x)
                feat_of[r["clause_id"]] = r["feat"]
    teacher = None
    if distill:
        teacher = {}
        for x in open(f"{DATA}/teacher.jsonl", encoding="utf-8"):
            if x.strip():
                r = json.loads(x)
                teacher[(r["qid"], r["clause"])] = r["score"]
    preds = train_and_predict(rows, fold, base_model, easy_per_hard=easy_per_hard, epochs=epochs, bs=bs,
                              feat_of=feat_of, teacher=teacher)
    print(f"[fold {fold}] {base_model} (features={use_features}, distill={distill}): {len(preds)} preds", flush=True)
    return preds


@app.local_entrypoint()
def main(model: str = "minilm", smoke: bool = False, easy_per_hard: int = 1, features: bool = False,
         distill: bool = False, tag: str = ""):
    base = BASES[model]
    with vol.batch_upload(force=True) as up:  # push the dataset(s) to the volume the GPU tasks read
        up.put_file("data/models/ce/dataset.jsonl", "/dataset.jsonl")
        if features:
            up.put_file("data/models/ce/clause_features.jsonl", "/clause_features.jsonl")
        if distill:  # the Gemma teacher scores (condensed pointwise) = the no-gold training signal
            up.put_file("data/models/condensed_scores.jsonl", "/teacher.jsonl")
    folds = [0] if smoke else [0, 1, 2, 3, 4]
    epochs = 1 if smoke else 2
    call_args = [(f, base, easy_per_hard, epochs, 32, features, distill) for f in folds]
    all_preds = []
    for preds in train_fold.starmap(call_args):  # one A10 task per fold, in parallel
        all_preds.extend(preds)
    default = ("distillfeat" if features else "distill") if distill else ("hardfeat" if features else "hard")
    suffix = "_smoke" if smoke else f"_{tag or default}"
    out = Path(f"data/models/ce/preds_{model}{suffix}.jsonl")
    out.write_text("\n".join(json.dumps(p) for p in all_preds), encoding="utf-8")
    print(f"wrote {len(all_preds)} held-out preds -> {out}")
