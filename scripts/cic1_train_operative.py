"""CIC-1c: train the OPERATIVE-RULE binary classifier (is a span a binding rule, or descriptive/definition/
cross-reference?) on Modal, reusing the setfit skill's hardened patterns (ProgressCallback X/N + loss, per-class
floor, data_sha fingerprint, registry.jsonl, save-to-volume). Ephemeral `modal run` (auto-stops, no orphaned GPU).

  uv run --no-sync modal run scripts/cic1_train_operative.py                         # default backbone
  uv run --no-sync modal run scripts/cic1_train_operative.py --backbone BAAI/bge-small-en-v1.5 --tag bge

Data (section-disjoint, symmetric test) is prepped locally by scripts/cic1_prep_operative.py and passed as args
(small). Model + metrics + registry land on the `cic1-operative` volume."""
from __future__ import annotations

import json
from pathlib import Path

import modal

app = modal.App("cic1-operative")
image = (modal.Image.debian_slim(python_version="3.12")
         .pip_install("setfit", "torch", "scikit-learn", "sentencepiece")
         .env({"PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True"}))
vol = modal.Volume.from_name("cic1-operative", create_if_missing=True)
GPU = "L4"
DATA = "/data"
LOCAL = Path("/Users/farhan/work/RAG_Wright/data/compliance/cic1_labels")


@app.function(image=image, gpu=GPU, volumes={DATA: vol}, timeout=3600)
def train_fn(train_rows: list, test_rows: list, backbone: str, num_iterations: int,
             num_epochs: int, batch_size: int, max_seq: int, tag: str) -> dict:
    import hashlib
    import time
    from collections import Counter

    from datasets import Dataset
    from setfit import SetFitModel, Trainer, TrainingArguments
    from sklearn.metrics import accuracy_score, f1_score
    from transformers import TrainerCallback

    vol.reload()
    labels = sorted({r["label"] for r in train_rows})  # ['non', 'rule']

    class ProgressCallback(TrainerCallback):
        def on_train_begin(self, args, state, control, **kw):
            self.t0 = time.time(); self.total = int(getattr(state, "max_steps", 0) or 0)
            print(f"[cic1-{tag}] TRAIN START: {self.total} steps batch={args.batch_size} gpu={GPU}", flush=True)

        def on_log(self, args, state, control, logs=None, **kw):
            loss = (logs or {}).get("embedding_loss", (logs or {}).get("loss"))
            if loss is None or not hasattr(self, "t0"):
                return
            el = time.time() - self.t0; step = int(state.global_step); tot = max(self.total, 1)
            print(f"[cic1-{tag}] step {step}/{tot} ({100*step/tot:.0f}%) loss={loss} elapsed={el:.0f}s", flush=True)

    model = SetFitModel.from_pretrained(backbone, labels=labels, device="cuda")
    if getattr(model, "model_body", None) is not None:
        model.model_body.max_seq_length = max_seq
    args = TrainingArguments(batch_size=batch_size, num_epochs=num_epochs, num_iterations=num_iterations,
                             eval_strategy="no", save_strategy="no", logging_steps=10)
    train_ds = Dataset.from_list([{"text": r["text"], "label": r["label"]} for r in train_rows])
    trainer = Trainer(model=model, args=args, callbacks=[ProgressCallback()],
                      train_dataset=train_ds, column_mapping={"text": "text", "label": "label"})
    t0 = time.time(); trainer.train(); train_s = time.time() - t0

    model_dir = f"{DATA}/models/cic1_operative_{tag}"
    model.save_pretrained(model_dir); vol.commit()

    gold = [r["label"] for r in test_rows]
    preds = [str(p) for p in model.predict([r["text"] for r in test_rows])]
    n = Counter(gold); ok = Counter(g for g, p in zip(gold, preds) if g == p)
    per_class = {l: {"n": n[l], "recall": round(ok[l] / n[l], 4)} for l in n}
    floor = min((ok[l] / n[l] for l in n), default=0.0)
    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    data_sha = hashlib.sha1("\n".join(sorted(f"{r['label']}\t{r['text']}" for r in train_rows)).encode()).hexdigest()[:12]
    res = {"framework": "setfit", "task": "operative_rule", "run_id": run_id, "data_sha": data_sha,
           "backbone": backbone, "tag": tag, "model_path": model_dir,
           "train_examples": len(train_rows), "test_examples": len(test_rows),
           "overall_accuracy": round(accuracy_score(gold, preds), 4),
           "macro_f1": round(f1_score(gold, preds, average="macro", zero_division=0), 4),
           "floor": round(floor, 4), "per_class": per_class,
           "train_seconds": round(train_s, 1)}
    Path(f"{DATA}/results").mkdir(exist_ok=True)
    Path(f"{DATA}/results/cic1_operative_{tag}.json").write_text(json.dumps(res, indent=2))
    with Path(f"{DATA}/registry.jsonl").open("a") as f:
        f.write(json.dumps({k: res[k] for k in ("run_id", "tag", "backbone", "data_sha", "floor",
                                                 "overall_accuracy", "macro_f1", "model_path")}) + "\n")
    vol.commit()
    print(f"[cic1-{tag}] DONE train={train_s:.0f}s acc={res['overall_accuracy']} macro_f1={res['macro_f1']} "
          f"floor={res['floor']} per_class={per_class}", flush=True)
    return res


@app.function(image=image, gpu=GPU, volumes={DATA: vol}, timeout=1800)
def eval_dump_fn(test_rows: list, tag: str) -> dict:
    """Reload a saved model and DUMP the misclassified test cases (error analysis for CIC-1c)."""
    from setfit import SetFitModel
    model = SetFitModel.from_pretrained(f"{DATA}/models/cic1_operative_{tag}")
    preds = [str(p) for p in model.predict([r["text"] for r in test_rows])]
    wrong = [{"gold": r["label"], "pred": p, "text": r["text"]}
             for r, p in zip(test_rows, preds) if r["label"] != p]
    return {"tag": tag, "n_test": len(test_rows), "n_wrong": len(wrong), "wrong": wrong}


@app.function(image=image, gpu=GPU, volumes={DATA: vol}, timeout=1800)
def ensemble_fn(test_rows: list, tags: list) -> dict:
    """Average predict_proba across several saved models (complementary backbones), argmax -> label. Per-class
    recall + the misclassified cases (for the next data round)."""
    import numpy as np
    from setfit import SetFitModel
    from collections import Counter
    texts = [r["text"] for r in test_rows]
    gold = [r["label"] for r in test_rows]
    labels = ["non", "rule"]  # SetFit sorts labels; consistent across models
    probs = None
    for t in tags:
        m = SetFitModel.from_pretrained(f"{DATA}/models/cic1_operative_{t}")
        p = np.asarray(m.predict_proba(texts), dtype=float)
        probs = p if probs is None else probs + p
    preds = [labels[i] for i in probs.argmax(1)]
    n = Counter(gold); ok = Counter(g for g, p in zip(gold, preds) if g == p)
    per_class = {l: {"n": n[l], "recall": round(ok[l] / n[l], 4)} for l in n}
    wrong = [{"gold": g, "pred": p, "text": r["text"]} for r, g, p in zip(test_rows, gold, preds) if g != p]
    return {"tags": tags, "accuracy": round(sum(ok.values()) / sum(n.values()), 4),
            "floor": round(min(ok[l] / n[l] for l in n), 4), "per_class": per_class, "wrong": wrong}


@app.function(image=image, gpu=GPU, volumes={DATA: vol}, timeout=1800)
def proba_fn(test_rows: list, tag: str) -> list:
    import numpy as np
    from setfit import SetFitModel
    m = SetFitModel.from_pretrained(f"{DATA}/models/cic1_operative_{tag}")
    labels = ["non", "rule"]
    p = np.asarray(m.predict_proba([r["text"] for r in test_rows]), dtype=float)
    ri = labels.index("rule")
    return [{"text": r["text"], "gold": r["label"], "proba_rule": float(p[i][ri]),
             "pred": labels[int(p[i].argmax())]} for i, r in enumerate(test_rows)]


@app.local_entrypoint()
def proba(tag: str = "bge_v3b", out: str = "data/compliance/cic1_labels/proba.json") -> None:
    import json as _j
    test = [_j.loads(l) for l in (LOCAL / "operative_test.jsonl").read_text().splitlines() if l.strip()]
    from pathlib import Path as _P
    _P(out).write_text(_j.dumps(proba_fn.remote(test, tag), indent=2))
    print(f"wrote {out}", flush=True)


@app.local_entrypoint()
def ensemble(tags: str = "bge_v2,minilm_v2") -> None:
    import json as _j
    test = [_j.loads(l) for l in (LOCAL / "operative_test.jsonl").read_text().splitlines() if l.strip()]
    res = ensemble_fn.remote(test, [t.strip() for t in tags.split(",")])
    print(f"[ensemble {res['tags']}] acc={res['accuracy']} floor={res['floor']} per_class={res['per_class']}", flush=True)
    print(f"  {len(res['wrong'])} wrong:", flush=True)
    for w in res["wrong"]:
        print(f"   gold={w['gold']:<4} pred={w['pred']:<4} :: {w['text'][:100]}", flush=True)


@app.local_entrypoint()
def eval_dump(tag: str = "mpnet_gen") -> None:
    import json as _j
    test = [_j.loads(l) for l in (LOCAL / "operative_test.jsonl").read_text().splitlines() if l.strip()]
    res = eval_dump_fn.remote(test, tag)
    print(f"[dump] {tag}: {res['n_wrong']}/{res['n_test']} wrong", flush=True)
    for w in res["wrong"]:
        print(f"  gold={w['gold']:<4} pred={w['pred']:<4} :: {w['text'][:110]}", flush=True)


@app.local_entrypoint()
def main(backbone: str = "sentence-transformers/all-MiniLM-L6-v2", num_iterations: int = 20,
         num_epochs: int = 1, batch_size: int = 16, max_seq: int = 128, tag: str = "minilm") -> None:
    train = [json.loads(l) for l in (LOCAL / "operative_train.jsonl").read_text().splitlines() if l.strip()]
    test = [json.loads(l) for l in (LOCAL / "operative_test.jsonl").read_text().splitlines() if l.strip()]
    print(f"[local] train={len(train)} test={len(test)} backbone={backbone} iters={num_iterations}", flush=True)
    res = train_fn.remote(train, test, backbone, num_iterations, num_epochs, batch_size, max_seq, tag)
    print("[local] RESULT:\n" + json.dumps(res, indent=2), flush=True)
