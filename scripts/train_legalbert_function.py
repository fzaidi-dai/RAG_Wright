"""T56 (FR-R, ADR-0025): fine-tune LegalBERT for the function classifier (per-type F1).

CUAD contracts -> operative spans (T55) -> labels (max-overlap CUAD type, else NONE) -> fine-tune
`nlpaueb/legal-bert-base-uncased` (sequence classification, MPS) -> per-type F1 + the confusion among the
confusable types. Contract-disjoint split (no leakage). Run in the background (downloads ~440MB on first run,
then trains on MPS).

  LIMIT=6 uv run python -m scripts.train_legalbert_function   # dry-run: download + prove the pipeline
  uv run python -m scripts.train_legalbert_function           # full run
"""

from __future__ import annotations

import json
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
    EarlyStoppingCallback,
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
            epoch = state.epoch if state.epoch is not None else 0.0  # pre-train resume eval has no epoch yet
            self._append(f"EVAL epoch {epoch:.2f}  macro_f1={metrics.get('eval_macro_f1', 0):.4f}  "
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
CUAD = Path("data/cuad/extracted/CUAD_v1.json")
NEW_FUNCS = Path("data/models/new_function_spans.jsonl")  # T60 LLM-bootstrapped extended-class spans
SEED = 0
LIMIT = int(os.environ.get("LIMIT", "0"))
# A dry-run (LIMIT) is fully ISOLATED to its own dir so it can NEVER clobber the production model.
OUT = Path("data/models/legalbert_function_dryrun" if LIMIT else "data/models/legalbert_function")
# INIT_FROM: continue from an existing fine-tuned checkpoint (weights only) instead of the base model.
# A converged checkpoint sits at LR ~0, so continuation uses a LOW CONSTANT LR with no warmup (a fresh
# warmup back to 2e-5 would jolt and destabilize it). Best-eval-loss retention + early stopping decide
# how many more epochs actually help, and (resume only) the result is adopted ONLY if it beats the start.
INIT_FROM = os.environ.get("INIT_FROM", "")
_RESUME = bool(INIT_FROM)
EPOCHS = int(os.environ.get("EPOCHS", "1" if LIMIT else ("3" if _RESUME else "4")))
LR = float(os.environ.get("LR", "5e-6" if _RESUME else "2e-5"))
WARMUP = float(os.environ.get("WARMUP", "0.0" if _RESUME else "0.1"))
# resume: a CONSTANT low LR so early stopping (not a schedule decaying to 0) decides when to stop; base:
# the standard linear decay after warmup.
LR_SCHED = os.environ.get("LR_SCHED", "constant" if _RESUME else "linear")
PATIENCE = int(os.environ.get("PATIENCE", "1"))  # stop after this many evals with no eval_loss improvement
PER_TYPE = 20 if LIMIT else 400  # cap positives per type; NONE capped to the non-NONE total (balance)


def _add_new_functions(tr_texts, tr_labels, te_texts, te_labels, test_ids: set[str]) -> tuple[int, int]:
    """Fold the T60 new-function spans into the split by their source contract (same contract-disjoint
    rule as the CUAD spans -- a span from a test contract goes to test, so there is no leakage)."""
    if not NEW_FUNCS.exists():
        return 0, 0
    n_tr = n_te = 0
    for line in NEW_FUNCS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row["contract_id"] in test_ids:
            te_texts.append(row["text"])
            te_labels.append(row["label"])
            n_te += 1
        else:
            tr_texts.append(row["text"])
            tr_labels.append(row["label"])
            n_tr += 1
    return n_tr, n_te


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
    test_ids = {c.contract_id for c in test_c}
    n_new_tr, n_new_te = _add_new_functions(tr_texts, tr_labels, te_texts, te_labels, test_ids)
    labs = sorted(set(tr_labels) | set(te_labels))
    label2id = {label: i for i, label in enumerate(labs)}
    id2label = {i: label for label, i in label2id.items()}
    print(f"contracts train/test={len(train_c)}/{len(test_c)}  spans={len(tr_texts)}/{len(te_texts)}  "
          f"labels={len(labs)}  epochs={EPOCHS}  (new-function spans tr/te={n_new_tr}/{n_new_te})", flush=True)

    init_from = INIT_FROM or MODEL  # base model, or a fine-tuned checkpoint to continue from (weights only)
    tok = AutoTokenizer.from_pretrained(init_from)

    def tokenize(batch):
        return tok(batch["text"], truncation=True, max_length=256)

    tr = Dataset.from_dict({"text": tr_texts, "label": [label2id[x] for x in tr_labels]}).map(tokenize, batched=True)
    te = Dataset.from_dict({"text": te_texts, "label": [label2id[x] for x in te_labels]}).map(tokenize, batched=True)
    # A resume checkpoint already has a 45-class head matching this deterministic split's label order; the
    # base model gets a fresh head. Either way num_labels/id2label are asserted to match at load.
    model = AutoModelForSequenceClassification.from_pretrained(
        init_from, num_labels=len(labs), id2label=id2label, label2id=label2id
    )

    def metrics(eval_pred):
        logits, gold = eval_pred
        preds = np.argmax(logits, axis=-1)
        return {"macro_f1": f1_score(gold, preds, average="macro", zero_division=0)}

    args = TrainingArguments(
        output_dir=str(OUT / "_trainer"), num_train_epochs=EPOCHS, per_device_train_batch_size=16,
        per_device_eval_batch_size=32, eval_strategy="epoch", logging_steps=50,
        report_to="none", fp16=False, bf16=False,
        # LR recipe: base run uses lower-LR + warmup + weight decay (the Trainer default 5e-5/no-warmup
        # plateaued); a resume run uses a low CONSTANT LR (see INIT_FROM) to continue a converged model.
        learning_rate=LR, warmup_ratio=WARMUP, weight_decay=0.01, lr_scheduler_type=LR_SCHED,
        # PRODUCTION policy: checkpoint every epoch, keep only the BEST by eval_loss (prune the rest), and
        # reload the best at the end -- a later, worse epoch can never be what we save.
        save_strategy="epoch", save_total_limit=2,
        load_best_model_at_end=True, metric_for_best_model="eval_loss", greater_is_better=False,
    )
    train_ids = [label2id[x] for x in tr_labels]
    weights = compute_class_weight("balanced", classes=np.arange(len(labs)), y=np.array(train_ids))
    trainer = WeightedTrainer(model, args, train_dataset=tr, eval_dataset=te,
                              data_collator=DataCollatorWithPadding(tok), compute_metrics=metrics,
                              class_weights=torch.tensor(weights, dtype=torch.float),
                              callbacks=[FileProgress(OUT / "progress.log"),
                                         EarlyStoppingCallback(early_stopping_patience=PATIENCE)])
    # Resume guard: measure the starting weights first, so we can adopt the continuation ONLY if it wins.
    start_loss = trainer.evaluate()["eval_loss"] if _RESUME else None
    if _RESUME:
        print(f"resume: start eval_loss={start_loss:.4f} (from {INIT_FROM})", flush=True)
    t0 = time.perf_counter()
    trainer.train()
    best_loss = trainer.state.best_metric  # best eval_loss seen; load_best_model_at_end has reloaded it
    best_str = f"{best_loss:.4f}" if best_loss is not None else "n/a"
    print(f"\ntrained in {time.perf_counter()-t0:.0f}s  best eval_loss={best_str}", flush=True)

    gold = [label2id[x] for x in te_labels]
    preds = np.argmax(trainer.predict(te).predictions, axis=-1)
    print("\n" + classification_report(
        gold, preds, labels=list(range(len(labs))), target_names=labs, zero_division=0
    ))
    print(f"MACRO-F1={f1_score(gold, preds, average='macro', zero_division=0):.3f}  "
          f"micro-F1={f1_score(gold, preds, average='micro', zero_division=0):.3f}", flush=True)

    key = [k for k in ["Cap On Liability", "Uncapped Liability", "Insurance", "Liquidated Damages",
                       "Warranty Duration", "Indemnification", "Indirect/Consequential Damages Waiver",
                       "Warranty Disclaimer", NONE_LABEL] if k in set(te_labels)]
    cm = confusion_matrix(gold, preds, labels=[label2id[k] for k in key])
    print(f"\nconfusion (rows=gold, cols=pred) {key}:")
    for row, k in zip(cm, key):
        print(f"  {k:22s} {list(int(x) for x in row)}")

    # Adopt guard: in resume mode, overwrite the production model ONLY if the continuation actually beat
    # the starting weights (so "best never overwritten by worse" holds across the resume boundary too).
    if _RESUME and start_loss is not None and best_loss is not None and best_loss >= start_loss:
        print(f"\ncontinuation did NOT improve (best {best_loss:.4f} >= start {start_loss:.4f}); "
              f"KEEPING the existing model, NOT overwriting {OUT}", flush=True)
        return

    OUT.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(OUT)
    tok.save_pretrained(OUT)
    print(f"\nsaved {OUT}  (best eval_loss={best_str})", flush=True)


if __name__ == "__main__":
    main()
