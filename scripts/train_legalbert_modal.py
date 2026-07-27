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
    .pip_install("torch", "transformers", "datasets", "accelerate", "scikit-learn", "numpy", "sentencepiece")
)
vol = modal.Volume.from_name("legalbert-function", create_if_missing=True)
DATA = "/data"
BASE = "nlpaueb/legal-bert-base-uncased"

# Step-5: confusable-sibling families derived data-drivenly from the current model's holdout confusion
# (>=0.12 of a class's gold spans predicted as the sibling; connected components). The sibling-margin loss
# forces separation on exactly these pairs. Labels are the training label space (CUAD casing, e.g. "Ip").
SIBLING_GROUPS = [
    ["Affiliate License-Licensee", "Competitive Restriction Exception", "Exclusivity",
     "Irrevocable Or Perpetual License", "License Grant", "Non-Transferable License"],
    ["Anti-Assignment", "Change Of Control"],
    ["Effective Date", "Expiration Date"],
    ["Unlimited/All-You-Can-Eat-License", "Volume Restriction"],
    ["Affiliate License-Licensor", "Ip Ownership Assignment", "Joint Ip Ownership"],
    ["Notice Period To Terminate Renewal", "Price Restrictions", "Renewal Term", "Termination For Convenience"],
    ["Most Favored Nation", "No-Solicit Of Customers", "Non-Compete"],
    ["Cap On Liability", "Uncapped Liability"],
    ["Liquidated Damages", "Revenue/Profit Sharing"],
    ["Indemnification", "No-Solicit Of Employees", "Third Party Beneficiary"],
]
LOCAL_MODEL = Path("data/models/legalbert_function")
STAGING = Path("data/models/legalbert_function_staging")
LOGITS_NPZ = Path("data/models/holdout_logits.npz")


@app.function(image=image, gpu="A10", volumes={DATA: vol}, timeout=14400)
def train_fn(base: str, epochs: int, lr: float, warmup: float, lr_sched: str, patience: int, resume: bool,
             sib_lambda: float = 0.0, sib_margin: float = 2.0, save_tag: str = "", precision: str = "bf16",
             continue_from: str = "", ckpt_tag: str = "") -> dict:
    """Train `base` (or `continue_from` a saved model) on the uploaded split. RESUMABLE (ADR-0030 skill): per-
    epoch checkpoints to /ckpt/<ckpt_tag> on the Volume, committed after each epoch, so a crash resumes from
    the last checkpoint (never from scratch). Keeps best-by-macro-F1; ALWAYS persists it to /models/<save_tag>.
    Evaluates on the FULL holdout (mean non-NONE recall + per-span preds). Returns metrics + preds + whether
    it CONVERGED (early-stop fired, or best epoch < last epoch) + epochs_ran, so the caller can continue-to-
    convergence."""
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
    from transformers.trainer_utils import get_last_checkpoint

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

        def on_save(self, args, state, control, **k):
            vol.commit()  # persist the just-written epoch checkpoint to the Volume -> crash-resumable

    # sibling adjacency mask (K,K): sib_mask[c] = the confusable siblings of class c (step-5 hard negatives)
    sib_mask = torch.zeros(len(labs), len(labs))
    for group in SIBLING_GROUPS:
        ids = [label2id[l] for l in group if l in label2id]
        for a in ids:
            for b in ids:
                if a != b:
                    sib_mask[a, b] = 1.0

    class WeightedTrainer(Trainer):
        def __init__(self, *a, class_weights, sibling_mask, sib_lambda, sib_margin, **k):
            super().__init__(*a, **k)
            self._w = class_weights
            self._sib = sibling_mask
            self._lam = sib_lambda
            self._margin = sib_margin

        def compute_loss(self, model, inputs, return_outputs=False, **k):
            labels = inputs.pop("labels")
            out = model(**inputs)
            logits = out.logits
            loss = torch.nn.functional.cross_entropy(
                logits, labels, weight=self._w.to(logits.device, logits.dtype))
            if self._lam > 0:
                # sibling-margin hinge: push logit[true] >= logit[sibling] + margin over each example's siblings
                sib = self._sib.to(logits.device)[labels]  # (B, K) 1 where a sibling of the true class
                true_logit = logits.gather(1, labels.unsqueeze(1))  # (B, 1)
                hinge = torch.nn.functional.relu(self._margin - (true_logit - logits))  # (B, K)
                denom = sib.sum(1).clamp(min=1.0)
                loss = loss + self._lam * ((hinge * sib).sum(1) / denom).mean()
            return (loss, out) if return_outputs else loss

    init_from = continue_from or (f"{DATA}/model" if resume else base)
    continuing = bool(continue_from or resume)
    tok = AutoTokenizer.from_pretrained(init_from)
    if tok.pad_token is None:  # decoder models (Qwen etc.) have no pad token by default
        tok.pad_token = tok.eos_token

    def tok_fn(b):
        return tok(b["text"], truncation=True, max_length=256)

    tr = Dataset.from_dict({"text": tr_texts, "label": [label2id[x] for x in tr_labels]}).map(tok_fn, batched=True)
    te = Dataset.from_dict({"text": te_texts, "label": [label2id[x] for x in te_labels]}).map(tok_fn, batched=True)
    # force fp32 load: some checkpoints (DeBERTa-v3) carry torch_dtype=float16 in their config, which would
    # otherwise load the model in fp16 and destabilize full-precision training.
    model = AutoModelForSequenceClassification.from_pretrained(
        init_from, num_labels=len(labs), id2label=id2label, label2id=label2id, torch_dtype=torch.float32)
    if model.config.pad_token_id is None:  # decoder classification needs the pad id to find the last token
        model.config.pad_token_id = tok.pad_token_id

    def metrics(ep):
        logits, gold = ep
        return {"macro_f1": f1_score(gold, np.argmax(logits, -1), average="macro", zero_division=0)}

    ckpt_dir = f"{DATA}/ckpt/{ckpt_tag or save_tag or 'prod'}"  # durable, per-run checkpoint dir on the Volume
    args = TrainingArguments(
        output_dir=ckpt_dir, num_train_epochs=epochs, per_device_train_batch_size=16,
        per_device_eval_batch_size=64, eval_strategy="epoch", logging_steps=50, report_to="none",
        bf16=(precision == "bf16"), fp16=(precision == "fp16"), max_grad_norm=1.0,
        learning_rate=lr, warmup_ratio=warmup, weight_decay=0.01, lr_scheduler_type=lr_sched,
        save_strategy="epoch", save_total_limit=2,
        load_best_model_at_end=True, metric_for_best_model="eval_macro_f1", greater_is_better=True)
    weights = compute_class_weight("balanced", classes=np.arange(len(labs)),
                                   y=np.array([label2id[x] for x in tr_labels]))
    trainer = WeightedTrainer(
        model, args, train_dataset=tr, eval_dataset=te, data_collator=DataCollatorWithPadding(tok),
        compute_metrics=metrics, class_weights=torch.tensor(weights, dtype=torch.float),
        sibling_mask=sib_mask, sib_lambda=sib_lambda, sib_margin=sib_margin,
        callbacks=[FileProgress(), EarlyStoppingCallback(early_stopping_patience=patience)])

    start_metric = trainer.evaluate()["eval_macro_f1"] if continuing else None
    t0 = time.perf_counter()
    # RESUME from the last committed checkpoint if this run was interrupted before (never restart from scratch)
    last_ckpt = get_last_checkpoint(ckpt_dir) if Path(ckpt_dir).is_dir() else None
    resumed = last_ckpt is not None
    if resumed:
        print(f"[resume] found checkpoint {last_ckpt} -> resuming (not restarting)", flush=True)
    trainer.train(resume_from_checkpoint=last_ckpt)
    best_metric = trainer.state.best_metric  # best eval_macro_f1 (higher is better)
    # CONVERGENCE: early-stopping fired (ran fewer than `epochs`), OR the best eval was NOT the last epoch.
    evals = [h["eval_macro_f1"] for h in trainer.state.log_history if "eval_macro_f1" in h]
    epochs_ran = round(trainer.state.epoch or 0)
    early_stopped = epochs_ran < epochs
    best_is_last = bool(evals) and max(range(len(evals)), key=evals.__getitem__) == len(evals) - 1
    converged = early_stopped or not best_is_last
    gold = [label2id[x] for x in te_labels]
    preds = np.argmax(trainer.predict(te).predictions, -1)
    macro = f1_score(gold, preds, average="macro", zero_division=0)
    report = classification_report(gold, preds, labels=list(range(len(labs))), target_names=labs,
                                   zero_division=0, output_dict=True)

    # full-holdout per-class recall (the real A/B metric) on the uploaded holdout spans
    he = json.loads(Path(f"{DATA}/holdout_eval.json").read_text())
    model.eval()
    hpreds: list[int] = []
    for i in range(0, len(he["texts"]), 128):
        enc = tok(he["texts"][i:i + 128], truncation=True, max_length=256, padding=True,
                  return_tensors="pt").to(model.device)
        with torch.no_grad():
            hpreds.extend(model(**enc).logits.argmax(-1).cpu().tolist())
    per: dict[str, list[int]] = {}
    for p, g in zip(hpreds, he["gold"]):
        if g == "NONE" or g not in label2id:
            continue
        d0 = per.setdefault(g, [0, 0])
        d0[1] += 1
        d0[0] += (id2label[p] == g)
    recs = {k: h / n for k, (h, n) in per.items() if n}
    mean_recall = sum(recs.values()) / len(recs) if recs else 0.0
    low = sorted(recs.items(), key=lambda kv: kv[1])[:12]

    # adopt when continuing only if the continuation's best macro-F1 beat the starting weights (higher better)
    adopt = not (continuing and start_metric is not None and best_metric is not None and best_metric <= start_metric)
    if adopt:  # ADR-0030: ALWAYS persist the best model (tagged so nothing is discarded or clobbered)
        out_dir = f"{DATA}/models/{save_tag}" if save_tag else f"{DATA}/model"
        model.save_pretrained(out_dir)
        tok.save_pretrained(out_dir)
        vol.commit()
    return {"macro_f1": macro, "mean_recall": mean_recall, "low": low, "best_metric": best_metric,
            "start_metric": start_metric, "adopted": adopt, "converged": converged, "epochs_ran": epochs_ran,
            "resumed": resumed, "secs": round(time.perf_counter() - t0), "report": report,
            "hpreds": [id2label[p] for p in hpreds], "progress": prog.read_text()}


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


@app.function(image=image, gpu="A10", volumes={DATA: vol}, timeout=1800)
def eval_fn(save_tag: str):
    """Re-score the SAVED tagged model (/models/<save_tag>) on the persisted full holdout. Used to get a
    holdout number + preds that MATCH the saved weights when an adopt-only-if-better continuation kept an
    earlier phase's model (so the training loop's returned preds -- from the later, non-adopted phase -- do
    NOT match what is on disk). Returns the same holdout fields as train_fn (mean_recall, holdout macro_f1,
    hpreds), computed from /models/<save_tag>."""
    import json as _json
    from pathlib import Path as _P

    import numpy as np
    import torch
    from sklearn.metrics import f1_score
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    mdir = f"{DATA}/models/{save_tag}"
    tok = AutoTokenizer.from_pretrained(mdir)
    model = AutoModelForSequenceClassification.from_pretrained(mdir, torch_dtype=torch.float32).to("cuda").eval()
    id2label = {i: model.config.id2label[i] for i in range(len(model.config.id2label))}
    label2id = {v: k for k, v in id2label.items()}
    he = _json.loads(_P(f"{DATA}/holdout_eval.json").read_text())
    hpreds: list[int] = []
    for i in range(0, len(he["texts"]), 128):
        enc = tok(he["texts"][i:i + 128], truncation=True, max_length=256, padding=True,
                  return_tensors="pt").to(model.device)
        with torch.no_grad():
            hpreds.extend(model(**enc).logits.argmax(-1).cpu().tolist())
    per: dict[str, list[int]] = {}
    for p, g in zip(hpreds, he["gold"]):
        if g == "NONE" or g not in label2id:
            continue
        d0 = per.setdefault(g, [0, 0])
        d0[1] += 1
        d0[0] += (id2label[p] == g)
    recs = {k: h / n for k, (h, n) in per.items() if n}
    mean_recall = sum(recs.values()) / len(recs) if recs else 0.0
    # holdout macro-F1 over non-NONE gold classes (mask NONE gold to keep it comparable to mean_recall)
    idx = [i for i, g in enumerate(he["gold"]) if g != "NONE" and g in label2id]
    g_ids = [label2id[he["gold"][i]] for i in idx]
    p_ids = [hpreds[i] for i in idx]
    holdout_macro = f1_score(g_ids, p_ids, average="macro", zero_division=0)
    return {"mean_recall": mean_recall, "holdout_macro_f1": holdout_macro,
            "hpreds": [id2label[p] for p in hpreds]}


def _push_local_model() -> None:
    """Upload the current LOCAL production model to the Volume /model (for logits or resume)."""
    with vol.batch_upload(force=True) as up:
        up.put_directory(str(LOCAL_MODEL), "/model")


def _download_model(dest: Path, remote: str = "model") -> None:
    """Pull a Volume model dir (`remote`, e.g. 'model' or 'models/<tag>') to a local dir (mandatory post-train
    safety copy). `modal volume get` lands it at <dest>/<basename>/*, so we flatten it up into <dest>."""
    dest.mkdir(parents=True, exist_ok=True)
    subprocess.run(["modal", "volume", "get", "--force", "legalbert-function", remote, str(dest)], check=True)
    inner = dest / Path(remote).name
    if inner.exists():
        for f in inner.iterdir():
            f.rename(dest / f.name)
        inner.rmdir()


AB_BASES = [  # the classifier A/B candidates (grounded research): current + contract-encoder + DeBERTa + small-LLM
    "nlpaueb/legal-bert-base-uncased",       # baseline (current)
    "nlpaueb/bert-base-uncased-contracts",   # Contracts-BERT (contract-pretrained, same family)
    "microsoft/deberta-v3-base",             # strong short-text / nuance encoder
    "Qwen/Qwen2.5-0.5B",                      # small decoder LLM (tests the LLM hypothesis)
]


def _upload_split_and_holdout(smoke: bool, resume: bool):
    from scripts.train_legalbert_function import build_holdout_spans, build_split

    s = build_split(limit=6 if smoke else 0)
    texts, gold, _ = build_holdout_spans()
    if smoke:  # keep the smoke fast end-to-end: tiny holdout eval too (just proves the recipe runs per base)
        texts, gold = texts[:500], gold[:500]
    Path("data/cache").mkdir(parents=True, exist_ok=True)
    Path("data/cache/legalbert_split.json").write_text(json.dumps(s))
    Path("data/cache/legalbert_holdout_eval.json").write_text(json.dumps({"texts": texts, "gold": gold}))
    with vol.batch_upload(force=True) as up:
        up.put_file("data/cache/legalbert_split.json", "/split.json")
        up.put_file("data/cache/legalbert_holdout_eval.json", "/holdout_eval.json")
        if resume:
            up.put_directory(str(LOCAL_MODEL), "/model")
    return s, len(texts)


@app.local_entrypoint()
def main(mode: str = "train", smoke: bool = False, resume: bool = False, epochs: int = 0,
         sib_lambda: float = 0.0, sib_margin: float = 2.0, bases: str = "", lr: float = 1e-5,
         patience: int = 2):
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

    ep = epochs or (1 if smoke else (3 if resume else 4))

    if mode == "eval":
        # Re-score SAVED tagged models on the holdout so preds/metrics MATCH the weights on disk (needed when a
        # non-adopted continuation left the loop's returned preds pointing at a different phase's model), and
        # pull each model down locally (ADR-0030 safety copy). --bases = comma-separated base ids (as trained).
        s, n_hold = _upload_split_and_holdout(smoke, resume=False)  # re-persist the SEED=0 holdout to the Volume
        preds_dir = Path("data/models/ab_preds")
        preds_dir.mkdir(parents=True, exist_ok=True)
        for b in [x for x in bases.split(",") if x] or AB_BASES:
            slug = b.replace("/", "_").replace(".", "-")
            r = eval_fn.remote(slug)
            old = json.loads((preds_dir / f"{slug}.json").read_text()) if (preds_dir / f"{slug}.json").exists() else {}
            old.update({"base": b, "mean_recall": r["mean_recall"], "holdout_macro_f1": r["holdout_macro_f1"],
                        "hpreds": r["hpreds"]})
            (preds_dir / f"{slug}.json").write_text(json.dumps(old))
            _download_model(Path(f"data/models/{slug}"), remote=f"models/{slug}")
            print(f"[eval] {b}: holdout mean_recall={r['mean_recall']:.3f} macro_f1={r['holdout_macro_f1']:.3f} "
                  f"-> preds {slug}.json rewritten, model -> data/models/{slug}", flush=True)
        return

    if mode == "quicktest":
        # MANDATORY pre-flight (skill best practice): prove save + commit + RESUME + load across two separate
        # Modal invocations, on a tiny model, before spending on real runs.
        _upload_split_and_holdout(smoke=True, resume=False)
        base = (bases.split(",")[0] if bases else "nlpaueb/legal-bert-base-uncased")
        for p in ("ckpt/quicktest", "models/quicktest"):  # clean slate so call-1 starts fresh
            subprocess.run(["modal", "volume", "rm", "-r", "legalbert-function", p],
                           capture_output=True)  # best-effort (may not exist)
        print("[quicktest] call 1: train 1 epoch -> save /models/quicktest + checkpoint /ckpt/quicktest", flush=True)
        r1 = train_fn.remote(base, 1, 2e-5, 0.1, "linear", 5, False, 0.0, 2.0, "quicktest", "fp32", "", "quicktest")
        print(f"[quicktest] call 1: epochs_ran={r1['epochs_ran']} resumed={r1.get('resumed')} "
              f"(expect resumed=False; saved best -> /models/quicktest)", flush=True)
        print("[quicktest] call 2: SAME ckpt_tag, epochs=2 -> MUST resume from the checkpoint (not restart)",
              flush=True)
        r2 = train_fn.remote(base, 2, 2e-5, 0.1, "linear", 5, False, 0.0, 2.0, "quicktest", "fp32", "", "quicktest")
        resumed = bool(r2.get("resumed"))
        dest = Path("data/models/quicktest_dl")
        _download_model(dest, remote="models/quicktest")
        from transformers import AutoModelForSequenceClassification
        loaded = AutoModelForSequenceClassification.from_pretrained(str(dest))
        loads_ok = loaded.config.num_labels > 0
        fresh_first = not r1.get("resumed")  # call 1 started fresh (clean slate)
        ok = fresh_first and resumed and r2["epochs_ran"] >= 2 and loads_ok
        print(f"\n[quicktest] call1_fresh={fresh_first}  call2_resumed={resumed}  "
              f"call2_epochs_ran={r2['epochs_ran']}  saved-model-loads={loads_ok} "
              f"({loaded.config.num_labels} labels)", flush=True)
        print(f"[quicktest] RESULT: {'PASS' if ok else 'FAIL'}", flush=True)
        return

    if mode == "ab":
        # A/B: ONE robust recipe for every base -- lower peak LR + warmup + linear decay + early-stopping +
        # best-by-macro-F1. Same split + holdout. Each model's best checkpoint IS saved to a tagged Volume
        # path (ADR-0030 -- nothing discarded, nothing clobbered), and per-span holdout predictions come back
        # for the confusion / miss-overlap / ensemble analysis.
        s, n_hold = _upload_split_and_holdout(smoke, resume=False)
        base_list = [b for b in bases.split(",") if b] or AB_BASES
        ab_ep = epochs or (1 if smoke else 6)
        preds_dir = Path("data/models/ab_preds")
        preds_dir.mkdir(parents=True, exist_ok=True)
        print(f"[ab] {len(base_list)} bases  spans tr={len(s['tr_texts'])} holdout={n_hold} "
              f"epochs={ab_ep} lr={lr} warmup=0.1 linear fp32 early-stop(patience={patience}) best=macro_f1",
              flush=True)
        cont_block = 5   # continuation block size (epochs) when not yet converged
        max_total = 25   # safety cap on total epochs per base
        results = {}
        for b in base_list:  # SEQUENTIAL, one base at a time (never concurrent)
            slug = b.replace("/", "_").replace(".", "-")
            print(f"\n[ab] === {b} (tag={slug}) ===", flush=True)
            try:
                # phase 1: from the base model, warmup+linear over ab_ep, fresh checkpoint dir
                r = train_fn.remote(b, ab_ep, lr, 0.1, "linear", patience, False, 0.0, 2.0, slug, "fp32",
                                    "", f"{slug}_p1")
                total, phase = r["epochs_ran"], 1
                # continue-until-convergence (DEFAULT): if still rising, load the saved best and train more
                # with a low CONSTANT LR (no warmup), until early-stopping/plateau or the total-epoch cap.
                while not r["converged"] and total < max_total:
                    phase += 1
                    print(f"[ab] {b}: NOT converged at {total} epochs (best still rising) -> continue "
                          f"phase {phase} (+{cont_block}ep, load /models/{slug}, lr=1e-5 constant)", flush=True)
                    r = train_fn.remote(b, cont_block, 1e-5, 0.0, "constant", patience, False, 0.0, 2.0,
                                        slug, "fp32", f"{DATA}/models/{slug}", f"{slug}_p{phase}")
                    total += r["epochs_ran"]
                results[b] = r
                (preds_dir / f"{slug}.json").write_text(json.dumps(
                    {"base": b, "mean_recall": r["mean_recall"], "macro_f1": r["macro_f1"],
                     "total_epochs": total, "converged": r["converged"], "hpreds": r["hpreds"]}))
                print(f"[ab] {b}: DONE total_epochs={total} converged={r['converged']} "
                      f"mean_recall={r['mean_recall']:.3f} macro_f1={r['macro_f1']:.3f}  "
                      f"model -> /models/{slug}, preds -> {slug}.json", flush=True)
            except Exception as e:  # noqa: BLE001 - one bad base must not sink the sweep
                print(f"[ab] {b}: FAILED {type(e).__name__}: {str(e)[:160]}", flush=True)
        print("\n=== A/B: mean non-NONE holdout recall ===", flush=True)
        for b, r in sorted(results.items(), key=lambda kv: -kv[1]["mean_recall"]):
            print(f"   {r['mean_recall']:.3f}  macroF1={r['macro_f1']:.3f}  {b}", flush=True)
        return

    # mode == train (single model)
    s, _ = _upload_split_and_holdout(smoke, resume)
    base = (bases.split(",")[0] if bases else "nlpaueb/legal-bert-base-uncased")
    lr = 5e-6 if resume else 2e-5
    warmup = 0.0 if resume else 0.1
    sched = "constant" if resume else "linear"
    print(f"[train] base={base} spans tr/te={len(s['tr_texts'])}/{len(s['te_texts'])} epochs={ep} "
          f"resume={resume} sib_lambda={sib_lambda}", flush=True)
    res = train_fn.remote(base, ep, lr, warmup, sched, 1, resume, sib_lambda, sib_margin, "", "bf16")
    print(f"\n[train] mean_recall={res['mean_recall']:.3f} macro_f1={res['macro_f1']:.3f} "
          f"adopted={res['adopted']} ({res['secs']}s)", flush=True)
    if not res["adopted"]:
        print("[train] continuation did NOT beat start -> Volume model unchanged; NOT downloading", flush=True)
        return
    dest = STAGING if not smoke else Path("data/models/legalbert_function_smoke")
    _download_model(dest)
    print(f"[train] best weights downloaded -> {dest}", flush=True)
