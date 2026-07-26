"""ADR-0030: LegalBERT function-classifier TRAINING + bulk LOGITS on Modal (A10). Lifts the local
train_legalbert_function.py logic (WeightedTrainer inverse-frequency loss, per-epoch best-eval-loss
checkpoint, early stopping, resume adopt-only-if-better) onto the GPU. The rag_wright DATA PREP
(parse_cuad -> label_operative_spans -> split) runs LOCALLY in the entrypoint and is uploaded, so the A10
image needs no rag_wright/docling. Best weights are ALWAYS downloaded back to a local STAGING dir after
training (durable local safety; GCS later) -- promotion to the production dir is a separate adopt-if-better
step. Grounded against the modal SDK in the framework graph (App / Image.pip_install / function(gpu=) /
Volume.from_name / batch_upload / .remote() / local_entrypoint), mirroring scripts/distill/train_modal.py.

  modal run scripts/train_legalbert_modal.py --smoke            # LIMIT split, 1 epoch (cheap end-to-end)
  modal run scripts/train_legalbert_modal.py                    # full train -> staging weights + metrics
  modal run scripts/train_legalbert_modal.py --mode logits      # push local model -> A10 holdout logits -> npz
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import modal

app = modal.App("legalbert-function")
image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("torch", "transformers", "datasets", "accelerate", "scikit-learn", "numpy")
)
vol = modal.Volume.from_name("legalbert-function", create_if_missing=True)
DATA = "/data"
BASE = "nlpaueb/legal-bert-base-uncased"
LOCAL_MODEL = Path("data/models/legalbert_function")
STAGING = Path("data/models/legalbert_function_staging")
LOGITS_NPZ = Path("data/models/holdout_logits.npz")


@app.function(image=image, gpu="A10", volumes={DATA: vol}, timeout=5400)
def train_fn(epochs: int, lr: float, warmup: float, lr_sched: str, patience: int, resume: bool) -> dict:
    """Train on the uploaded split; keep the best-eval-loss checkpoint; on resume adopt only if it beats the
    start. Saves the adopted model to the Volume at /model and returns metrics + the progress log."""
    import time

    import numpy as np
    import torch
    from datasets import Dataset
    from sklearn.metrics import classification_report, f1_score
    from sklearn.utils.class_weight import compute_class_weight
    from transformers import (
        AutoModelForSequenceClassification,
        AutoTokenizer,
        DataCollatorWithPadding,
        EarlyStoppingCallback,
        Trainer,
        TrainerCallback,
        TrainingArguments,
    )

    d = json.loads(Path(f"{DATA}/split.json").read_text())
    tr_texts, tr_labels = d["tr_texts"], d["tr_labels"]
    te_texts, te_labels = d["te_texts"], d["te_labels"]
    labs = d["labs"]
    label2id = {l: i for i, l in enumerate(labs)}
    id2label = {i: l for l, i in label2id.items()}
    prog = Path(f"{DATA}/progress.log")
    prog.write_text("")

    class FileProgress(TrainerCallback):
        def _a(self, s: str) -> None:
            with open(prog, "a") as f:
                f.write(s + "\n")
                f.flush()
            print(s, flush=True)

        def on_log(self, args, state, control, logs=None, **k):
            if logs and "loss" in logs:
                self._a(f"step {state.global_step} epoch {state.epoch:.2f} loss={logs['loss']:.4f} "
                        f"lr={logs.get('learning_rate', 0):.2e}")

        def on_evaluate(self, args, state, control, metrics=None, **k):
            if metrics:
                self._a(f"EVAL epoch {(state.epoch or 0):.2f} macro_f1={metrics.get('eval_macro_f1', 0):.4f} "
                        f"eval_loss={metrics.get('eval_loss', 0):.4f}")

    class WeightedTrainer(Trainer):
        def __init__(self, *a, class_weights, **k):
            super().__init__(*a, **k)
            self._w = class_weights

        def compute_loss(self, model, inputs, return_outputs=False, **k):
            labels = inputs.pop("labels")
            out = model(**inputs)
            loss = torch.nn.functional.cross_entropy(out.logits, labels, weight=self._w.to(out.logits.device))
            return (loss, out) if return_outputs else loss

    init_from = f"{DATA}/model" if resume else BASE
    tok = AutoTokenizer.from_pretrained(init_from)

    def tok_fn(b):
        return tok(b["text"], truncation=True, max_length=256)

    tr = Dataset.from_dict({"text": tr_texts, "label": [label2id[x] for x in tr_labels]}).map(tok_fn, batched=True)
    te = Dataset.from_dict({"text": te_texts, "label": [label2id[x] for x in te_labels]}).map(tok_fn, batched=True)
    model = AutoModelForSequenceClassification.from_pretrained(
        init_from, num_labels=len(labs), id2label=id2label, label2id=label2id)

    def metrics(ep):
        logits, gold = ep
        return {"macro_f1": f1_score(gold, np.argmax(logits, -1), average="macro", zero_division=0)}

    args = TrainingArguments(
        output_dir=f"{DATA}/_trainer", num_train_epochs=epochs, per_device_train_batch_size=16,
        per_device_eval_batch_size=64, eval_strategy="epoch", logging_steps=50, report_to="none", fp16=True,
        learning_rate=lr, warmup_ratio=warmup, weight_decay=0.01, lr_scheduler_type=lr_sched,
        save_strategy="epoch", save_total_limit=2,
        load_best_model_at_end=True, metric_for_best_model="eval_loss", greater_is_better=False)
    weights = compute_class_weight("balanced", classes=np.arange(len(labs)),
                                   y=np.array([label2id[x] for x in tr_labels]))
    trainer = WeightedTrainer(
        model, args, train_dataset=tr, eval_dataset=te, data_collator=DataCollatorWithPadding(tok),
        compute_metrics=metrics, class_weights=torch.tensor(weights, dtype=torch.float),
        callbacks=[FileProgress(), EarlyStoppingCallback(early_stopping_patience=patience)])

    start_loss = trainer.evaluate()["eval_loss"] if resume else None
    t0 = time.perf_counter()
    trainer.train()
    best_loss = trainer.state.best_metric
    gold = [label2id[x] for x in te_labels]
    preds = np.argmax(trainer.predict(te).predictions, -1)
    macro = f1_score(gold, preds, average="macro", zero_division=0)
    report = classification_report(gold, preds, labels=list(range(len(labs))), target_names=labs,
                                   zero_division=0, output_dict=True)

    adopt = not (resume and start_loss is not None and best_loss is not None and best_loss >= start_loss)
    if adopt:
        model.save_pretrained(f"{DATA}/model")
        tok.save_pretrained(f"{DATA}/model")
        vol.commit()
    return {"macro_f1": macro, "best_loss": best_loss, "start_loss": start_loss, "adopted": adopt,
            "secs": round(time.perf_counter() - t0), "report": report, "progress": prog.read_text()}


@app.function(image=image, gpu="A10", volumes={DATA: vol}, timeout=1800)
def logits_fn(texts: list[str]):
    """Forward pass over `texts` with the Volume /model -> (logits array, labels). Bulk GPU eval, results-back
    (the CE-distillation pattern), so nothing but the array crosses back."""
    import numpy as np
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(f"{DATA}/model")
    model = AutoModelForSequenceClassification.from_pretrained(f"{DATA}/model").to("cuda").eval()
    labels = [model.config.id2label[i] for i in range(len(model.config.id2label))]
    out = []
    for i in range(0, len(texts), 256):
        enc = tok(texts[i:i + 256], truncation=True, max_length=256, padding=True, return_tensors="pt").to("cuda")
        with torch.no_grad():
            out.append(model(**enc).logits.cpu().numpy())
    return np.vstack(out).astype("float32"), labels


def _push_local_model() -> None:
    """Upload the current LOCAL production model to the Volume /model (for logits or resume)."""
    with vol.batch_upload(force=True) as up:
        up.put_directory(str(LOCAL_MODEL), "/model")


def _download_model(dest: Path) -> None:
    """Pull the Volume /model to a local dir (mandatory post-train safety copy). `modal volume get` lands the
    remote 'model' dir at <dest>/model/*, so we flatten it up into <dest> (LegalBertFunctionClassifier.load
    expects the checkpoint files directly in the dir)."""
    dest.mkdir(parents=True, exist_ok=True)
    subprocess.run(["modal", "volume", "get", "--force", "legalbert-function", "model", str(dest)], check=True)
    inner = dest / "model"
    if inner.exists():
        for f in inner.iterdir():
            f.rename(dest / f.name)
        inner.rmdir()


@app.local_entrypoint()
def main(mode: str = "train", smoke: bool = False, resume: bool = False, epochs: int = 0):
    if mode == "logits":
        from scripts.train_legalbert_function import build_holdout_spans
        import numpy as np

        _push_local_model()
        texts, gold, cidx = build_holdout_spans()
        print(f"[logits] {len(texts)} holdout spans -> A10", flush=True)
        logits, labels = logits_fn.remote(texts)
        gold_ids = [list(labels).index(g) if g in labels else list(labels).index("NONE") for g in gold]
        LOGITS_NPZ.parent.mkdir(parents=True, exist_ok=True)
        np.savez(LOGITS_NPZ, logits=logits, gold=np.array(gold_ids), cidx=np.array(cidx), labels=np.array(labels))
        print(f"[logits] saved {LOGITS_NPZ}  shape={logits.shape}", flush=True)
        return

    # mode == train
    from scripts.train_legalbert_function import build_split

    s = build_split(limit=6 if smoke else 0)
    Path("data/cache").mkdir(parents=True, exist_ok=True)
    split_path = Path("data/cache/legalbert_split.json")
    split_path.write_text(json.dumps(s))
    with vol.batch_upload(force=True) as up:
        up.put_file(str(split_path), "/split.json")
        if resume:
            up.put_directory(str(LOCAL_MODEL), "/model")
    ep = epochs or (1 if smoke else (3 if resume else 4))
    lr = 5e-6 if resume else 2e-5
    warmup = 0.0 if resume else 0.1
    sched = "constant" if resume else "linear"
    print(f"[train] spans tr/te={len(s['tr_texts'])}/{len(s['te_texts'])} labels={len(s['labs'])} "
          f"epochs={ep} resume={resume} smoke={smoke}", flush=True)
    res = train_fn.remote(ep, lr, warmup, sched, 1, resume)
    print(f"\n[train] macro_f1={res['macro_f1']:.3f} best_loss={res['best_loss']} adopted={res['adopted']} "
          f"({res['secs']}s)", flush=True)
    if not res["adopted"]:
        print("[train] continuation did NOT beat start -> Volume model unchanged; NOT downloading", flush=True)
        return
    dest = STAGING if not smoke else Path("data/models/legalbert_function_smoke")
    _download_model(dest)
    print(f"[train] best weights downloaded -> {dest}  (promote to {LOCAL_MODEL} only if it beats the current "
          f"model on the holdout)", flush=True)
