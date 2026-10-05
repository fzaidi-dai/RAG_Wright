"""CIC-1c Laya training on Modal (T4), mirroring clause-classifier-ab/laya_modal.py::finetune_fn. Fine-tunes the
English Laya base (convaiinnovations/laya: ModernBERT-large + RL decision head) on the operative-rule data, then
evals per-class top-1 recall (the floor) on the real held-out test. Ephemeral `modal run` (auto-stops; no orphaned
GPU). The train/test rows are passed as args (small) and written to the volume inside the remote fn."""
from __future__ import annotations

import json
from pathlib import Path

import modal

app = modal.App("cic1-laya-operative")
FINETUNE_SCRIPT = "/Users/farhan/work/laya/research/scripts/finetune_single_device.py"
train_image = (modal.Image.debian_slim(python_version="3.11")
               .uv_pip_install("laya", "huggingface_hub", "safetensors", "transformers",
                               extra_index_url="https://download.pytorch.org/whl/cu124")
               .env({"HF_HUB_DISABLE_XET": "1", "OMP_NUM_THREADS": "4"})
               .add_local_file(FINETUNE_SCRIPT, "/root/finetune_single_device.py", copy=True))
vol = modal.Volume.from_name("cic1-laya-operative", create_if_missing=True)
hf_vol = modal.Volume.from_name("rw-hf-cache", create_if_missing=True)
DIM = "operative"
LOCAL = Path("/Users/farhan/work/RAG_Wright/data/compliance/cic1_labels/laya")


@app.function(image=train_image, gpu="T4", volumes={"/data": vol, "/root/.cache/huggingface": hf_vol}, timeout=10800)
def finetune_fn(train_rows: list, test_rows: list, epochs: int = 4) -> dict:
    import subprocess
    import time
    from collections import Counter
    from pathlib import Path as P

    from huggingface_hub import snapshot_download

    vol.reload()
    d = P(f"/data/laya/{DIM}"); d.mkdir(parents=True, exist_ok=True)
    (d / "train.jsonl").write_text("\n".join(json.dumps(r) for r in train_rows))
    (d / "test.jsonl").write_text("\n".join(json.dumps(r) for r in test_rows))
    base = snapshot_download("convaiinnovations/laya",
                             allow_patterns=["*.json", "*.safetensors", "encoder/*", "tokenizer/*"])
    out = f"/data/laya_models/{DIM}"
    print(f"[laya-{DIM}] base={base} train={len(train_rows)} test={len(test_rows)} epochs={epochs} -> {out}", flush=True)
    t0 = time.time()
    r = subprocess.run(["python", "/root/finetune_single_device.py", "--data", str(d / "train.jsonl"),
                        "--model-dir", base, "--output-dir", out, "--epochs", str(epochs)],
                       capture_output=True, text=True)
    print("STDOUT tail:\n", r.stdout[-2500:], flush=True)
    if r.returncode != 0:
        print("STDERR tail:\n", r.stderr[-3000:], flush=True)
        return {"status": "TRAIN_FAILED", "err": r.stderr[-800:]}
    train_s = time.time() - t0
    vol.commit()

    import laya
    agent = laya.load(out)
    n = Counter(); ok = Counter(); conf = 0.0
    for row in test_rows:
        gold = row["gold"][DIM]["label"]
        ans = agent.predict(row["state"], row["questions"])["answers"][DIM]
        n[gold] += 1
        ok[gold] += int(ans["choice"] == gold)
        conf += ans.get("confidence", 0.0)
    per_class = {v: {"n": n[v], "recall": round(ok[v] / n[v], 4)} for v in sorted(n)}
    floor = min((ok[v] / n[v] for v in n), default=0.0)
    acc = sum(ok.values()) / max(sum(n.values()), 1)
    res = {"status": "ok", "task": "operative_rule", "framework": "laya", "train_examples": len(train_rows),
           "test_examples": len(test_rows), "overall_accuracy": round(acc, 4), "floor": round(floor, 4),
           "per_class": per_class, "mean_confidence": round(conf / max(len(test_rows), 1), 4),
           "train_seconds": round(train_s, 1), "model_path": out}
    P("/data/results").mkdir(exist_ok=True)
    P(f"/data/results/laya_{DIM}.json").write_text(json.dumps(res, indent=2)); vol.commit()
    print(f"[laya-{DIM}] DONE acc={res['overall_accuracy']} floor={floor} per_class={per_class} "
          f"conf={res['mean_confidence']} train={train_s:.0f}s", flush=True)
    return res


@app.local_entrypoint()
def main(epochs: int = 4) -> None:
    train = [json.loads(l) for l in (LOCAL / "train.jsonl").read_text().splitlines() if l.strip()]
    test = [json.loads(l) for l in (LOCAL / "test.jsonl").read_text().splitlines() if l.strip()]
    print(f"[local] laya train={len(train)} test={len(test)} epochs={epochs}", flush=True)
    print("[local] RESULT:\n" + json.dumps(finetune_fn.remote(train, test, epochs), indent=2), flush=True)
