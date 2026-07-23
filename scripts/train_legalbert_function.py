"""T56 (FR-R, ADR-0025): fine-tune LegalBERT for the function classifier (per-type F1).

CUAD contracts -> operative spans (T55) -> labels (max-overlap CUAD type, else NONE) -> fine-tune
`nlpaueb/legal-bert-base-uncased` (sequence classification, MPS) -> per-type F1 + the confusion among the
confusable types. Contract-disjoint split (no leakage). Run in the background (downloads ~440MB on first run,
then trains on MPS).

  LIMIT=6 uv run python -m scripts.train_legalbert_function   # dry-run: download + prove the pipeline
  uv run python -m scripts.train_legalbert_function           # full run
"""

from __future__ import annotations

import os
import random
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import torch
from datasets import Dataset
from sklearn.metrics import classification_report, confusion_matrix, f1_score
from sklearn.utils.class_weight import compute_class_weight
from transformers import (
    AutoModelForSequenceClassification,
    AutoTokenizer,
    DataCollatorWithPadding,
    Trainer,
    TrainerCallback,
    TrainingArguments,
)

from rag_wright.spans.cuad_labels import label_operative_spans, parse_cuad
from rag_wright.spans.function_classifier import NONE_LABEL


class FileProgress(TrainerCallback):
    """Write loss/epoch + eval metrics to a FLUSHED progress file -- reliable tailing (stdout is pipe-buffered)."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text("")

    def _append(self, line: str) -> None:
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()

    def on_log(self, args, state, control, logs=None, **kwargs):
        if logs and "loss" in logs:
            self._append(f"step {state.global_step} epoch {state.epoch:.2f}  loss={logs['loss']:.4f}  "
                         f"lr={logs.get('learning_rate', 0):.2e}")

    def on_evaluate(self, args, state, control, metrics=None, **kwargs):
        if metrics:
            self._append(f"EVAL epoch {state.epoch:.2f}  macro_f1={metrics.get('eval_macro_f1', 0):.4f}  "
                         f"eval_loss={metrics.get('eval_loss', 0):.4f}")


class WeightedTrainer(Trainer):
    """Trainer with class-weighted cross-entropy (inverse-frequency) so the heavy NONE class does not collapse
    the model to majority prediction -- the LegalBERT analog of the linear head's class_weight='balanced'."""

    def __init__(self, *args, class_weights: torch.Tensor, **kwargs):
        super().__init__(*args, **kwargs)
        self._class_weights = class_weights

    def compute_loss(self, model, inputs, return_outputs=False, **kwargs):
        labels = inputs.pop("labels")
        outputs = model(**inputs)
        loss = torch.nn.functional.cross_entropy(
            outputs.logits, labels, weight=self._class_weights.to(outputs.logits.device)
        )
        return (loss, outputs) if return_outputs else loss

MODEL = "nlpaueb/legal-bert-base-uncased"
OUT = Path("data/models/legalbert_function")
CUAD = Path("data/cuad/extracted/CUAD_v1.json")
SEED = 0
LIMIT = int(os.environ.get("LIMIT", "0"))
EPOCHS = 1 if LIMIT else 4
PER_TYPE = 20 if LIMIT else 400  # cap positives per type; NONE capped to the non-NONE total (balance)


def _collect(contracts, per_type: int) -> tuple[list[str], list[str]]:
    by: dict[str, list[str]] = defaultdict(list)
    for c in contracts:
        for ls in label_operative_spans(c):
            by[ls.label].append(ls.text)
    rng = random.Random(SEED)
    non_none = sum(len(v) for k, v in by.items() if k != NONE_LABEL)
    texts: list[str] = []
    labels: list[str] = []
    for label, items in sorted(by.items()):
        cap = non_none if label == NONE_LABEL else per_type
        sample = items if len(items) <= cap else rng.sample(items, cap)
        texts += sample
        labels += [label] * len(sample)
    return texts, labels


def main() -> None:
    contracts = list(parse_cuad(CUAD))
    random.Random(SEED).shuffle(contracts)
    if LIMIT:
        contracts = contracts[:LIMIT]
    n_test = max(1, len(contracts) // 5)
    test_c, train_c = contracts[:n_test], contracts[n_test:]
    tr_texts, tr_labels = _collect(train_c, PER_TYPE)
    te_texts, te_labels = _collect(test_c, max(1, PER_TYPE // 3))
    labs = sorted(set(tr_labels) | set(te_labels))
    label2id = {label: i for i, label in enumerate(labs)}
    id2label = {i: label for label, i in label2id.items()}
    print(f"contracts train/test={len(train_c)}/{len(test_c)}  spans={len(tr_texts)}/{len(te_texts)}  "
          f"labels={len(labs)}  epochs={EPOCHS}", flush=True)

    tok = AutoTokenizer.from_pretrained(MODEL)

    def tokenize(batch):
        return tok(batch["text"], truncation=True, max_length=256)

    tr = Dataset.from_dict({"text": tr_texts, "label": [label2id[x] for x in tr_labels]}).map(tokenize, batched=True)
    te = Dataset.from_dict({"text": te_texts, "label": [label2id[x] for x in te_labels]}).map(tokenize, batched=True)
    model = AutoModelForSequenceClassification.from_pretrained(
        MODEL, num_labels=len(labs), id2label=id2label, label2id=label2id
    )

    def metrics(eval_pred):
        logits, gold = eval_pred
        preds = np.argmax(logits, axis=-1)
        return {"macro_f1": f1_score(gold, preds, average="macro", zero_division=0)}

    args = TrainingArguments(
        output_dir=str(OUT / "_trainer"), num_train_epochs=EPOCHS, per_device_train_batch_size=16,
        per_device_eval_batch_size=32, eval_strategy="epoch", save_strategy="no", logging_steps=50,
        report_to="none", fp16=False, bf16=False,
        # standard BERT fine-tune recipe: the Trainer default (5e-5, no warmup) plateaued (loss flat ~2.2);
        # MPS itself trains fine (overfit-a-batch verified). Lower LR + warmup + weight decay fixes it.
        learning_rate=2e-5, warmup_ratio=0.1, weight_decay=0.01,
    )
    train_ids = [label2id[x] for x in tr_labels]
    weights = compute_class_weight("balanced", classes=np.arange(len(labs)), y=np.array(train_ids))
    trainer = WeightedTrainer(model, args, train_dataset=tr, eval_dataset=te,
                              data_collator=DataCollatorWithPadding(tok), compute_metrics=metrics,
                              class_weights=torch.tensor(weights, dtype=torch.float),
                              callbacks=[FileProgress(OUT / "progress.log")])
    t0 = time.perf_counter()
    trainer.train()
    print(f"\ntrained in {time.perf_counter()-t0:.0f}s", flush=True)

    gold = [label2id[x] for x in te_labels]
    preds = np.argmax(trainer.predict(te).predictions, axis=-1)
    print("\n" + classification_report(
        gold, preds, labels=list(range(len(labs))), target_names=labs, zero_division=0
    ))
    print(f"MACRO-F1={f1_score(gold, preds, average='macro', zero_division=0):.3f}  "
          f"micro-F1={f1_score(gold, preds, average='micro', zero_division=0):.3f}", flush=True)

    key = [k for k in ["Cap On Liability", "Uncapped Liability", "Insurance", "Liquidated Damages",
                       "Warranty Duration", NONE_LABEL] if k in set(te_labels)]
    cm = confusion_matrix(gold, preds, labels=[label2id[k] for k in key])
    print(f"\nconfusion (rows=gold, cols=pred) {key}:")
    for row, k in zip(cm, key):
        print(f"  {k:22s} {list(int(x) for x in row)}")

    OUT.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(OUT)
    tok.save_pretrained(OUT)
    print(f"\nsaved {OUT}", flush=True)


if __name__ == "__main__":
    main()
