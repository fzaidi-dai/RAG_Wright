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


def train_and_predict(rows, held_fold, base_model, *, neg_ratio=8, epochs=2, bs=32, lr=2e-5, max_len=512):
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
        output_dir="/tmp/out", num_train_epochs=epochs, per_device_train_batch_size=bs,
        learning_rate=lr, warmup_ratio=0.1, fp16=True, report_to=[], logging_steps=50,
        save_strategy="no", dataloader_num_workers=2,
    )
    CrossEncoderTrainer(model=model, args=args, train_dataset=ds, loss=BinaryCrossEntropyLoss(model)).train()
    scores = model.predict([(r["query"], r["text"]) for r in test], batch_size=256, show_progress_bar=False)
    return [{"query_id": r["query_id"], "clause_id": r["clause_id"], "grade": r["grade"], "score": float(s)}
            for r, s in zip(test, scores)]


@app.function(image=image, gpu="A10", volumes={DATA: vol}, timeout=3600)
def train_fold(fold: int, base_model: str, neg_ratio: int, epochs: int, bs: int):
    rows = [json.loads(x) for x in open(f"{DATA}/dataset.jsonl", encoding="utf-8") if x.strip()]
    preds = train_and_predict(rows, fold, base_model, neg_ratio=neg_ratio, epochs=epochs, bs=bs)
    print(f"[fold {fold}] {base_model}: trained, {len(preds)} held-out preds", flush=True)
    return preds


@app.local_entrypoint()
def main(model: str = "minilm", smoke: bool = False, neg_ratio: int = 8):
    base = BASES[model]
    with vol.batch_upload(force=True) as up:  # push the dataset to the volume the GPU tasks read
        up.put_file("data/models/ce/dataset.jsonl", "/dataset.jsonl")
    folds = [0] if smoke else [0, 1, 2, 3, 4]
    epochs = 1 if smoke else 2
    call_args = [(f, base, neg_ratio, epochs, 32) for f in folds]
    all_preds = []
    for preds in train_fold.starmap(call_args):  # one A10 task per fold, in parallel
        all_preds.extend(preds)
    out = Path(f"data/models/ce/preds_{model}{'_smoke' if smoke else ''}.jsonl")
    out.write_text("\n".join(json.dumps(p) for p in all_preds), encoding="utf-8")
    print(f"wrote {len(all_preds)} held-out preds -> {out}")
