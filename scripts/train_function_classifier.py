"""T56 (FR-R, ADR-0025): train + evaluate the function classifier on CUAD (per-type F1).

CUAD contracts -> operative spans (T55 segmenter) -> labels (max-overlap CUAD type, else NONE) -> BGE-M3 dense
features -> LogisticRegression. Split is CONTRACT-DISJOINT (no leakage). Reports per-type precision/recall/F1
and the confusion among the confusable types (Cap On Liability vs Insurance vs Indemnification -- the whole
point). Heavy (BGE-M3 over ~20k spans); run in the background.

  LIMIT=8 uv run python -m scripts.train_function_classifier   # dry-run: prove the pipeline cheaply
  uv run python -m scripts.train_function_classifier            # full run
"""

from __future__ import annotations

import os
import random
import time
from collections import defaultdict
from pathlib import Path

from FlagEmbedding import BGEM3FlagModel
from sklearn.metrics import classification_report, confusion_matrix, f1_score

from rag_wright.packs.contracts.spans.cuad_labels import label_operative_spans, parse_cuad
from rag_wright.packs.contracts.spans.function_classifier import NONE_LABEL, FunctionClassifier

CUAD = Path("data/cuad/extracted/CUAD_v1.json")
MODEL_OUT = Path("data/models/function_clf.joblib")  # regenerable cache
SEED = 0
LIMIT = int(os.environ.get("LIMIT", "0"))  # >0 = use only this many contracts (dry-run)
PER_TYPE_TRAIN, NONE_TRAIN = 300, 5000
PER_TYPE_TEST, NONE_TEST = 100, 2000
if LIMIT:
    PER_TYPE_TRAIN, NONE_TRAIN, PER_TYPE_TEST, NONE_TEST = 20, 60, 10, 30


def _collect(contracts, per_type: int, none_cap: int) -> tuple[list[str], list[str]]:
    by_label: dict[str, list[str]] = defaultdict(list)
    for c in contracts:
        for ls in label_operative_spans(c):
            by_label[ls.label].append(ls.text)
    rng = random.Random(SEED)
    texts: list[str] = []
    labels: list[str] = []
    for label, items in sorted(by_label.items()):
        cap = none_cap if label == NONE_LABEL else per_type
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
    tr_texts, tr_labels = _collect(train_c, PER_TYPE_TRAIN, NONE_TRAIN)
    te_texts, te_labels = _collect(test_c, PER_TYPE_TEST, NONE_TEST)
    print(f"contracts train/test={len(train_c)}/{len(test_c)}  spans train/test={len(tr_texts)}/{len(te_texts)}  "
          f"types={len(set(tr_labels))}", flush=True)
    if not tr_texts or not te_texts:
        print("empty split; abort")
        return

    m = BGEM3FlagModel("BAAI/bge-m3", use_fp16=False)

    def emb(texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in m.encode(texts, return_dense=True, batch_size=32)["dense_vecs"]]

    t0 = time.perf_counter()
    x_tr, x_te = emb(tr_texts), emb(te_texts)
    print(f"embedded {len(x_tr)+len(x_te)} spans in {time.perf_counter()-t0:.0f}s", flush=True)

    clf = FunctionClassifier.train_on_features(x_tr, tr_labels)
    preds = clf.classify_features(x_te)
    print("\n" + classification_report(te_labels, preds, zero_division=0))
    print(f"MACRO-F1 = {f1_score(te_labels, preds, average='macro', zero_division=0):.3f}  "
          f"micro-F1 = {f1_score(te_labels, preds, average='micro', zero_division=0):.3f}", flush=True)

    key = ["Cap On Liability", "Uncapped Liability", "Insurance", "Liquidated Damages", "Warranty Duration", NONE_LABEL]
    key = [k for k in key if k in set(te_labels)]
    cm = confusion_matrix(te_labels, preds, labels=key)
    print(f"\nconfusion among confusable types (rows=gold, cols=pred): {key}")
    for row, k in zip(cm, key):
        print(f"  {k:22s} {list(row)}")

    MODEL_OUT.parent.mkdir(parents=True, exist_ok=True)
    clf.save(MODEL_OUT)
    print(f"\nsaved {MODEL_OUT}", flush=True)


if __name__ == "__main__":
    main()
