---
name: laya
description: Recipe for fine-tuning and serving a Laya (ModernBERT-large, RL-trained typed-decision) classifier to replace an LLM decision that a SetFit/encoder classifier PLATEAUS on. Use when confusable/relational/subjective closed-vocab values won't clear the bar with any encoder backbone. Covers uv install + env-isolation gotchas, the JSONL schema, per-value criteria, single-T4 fine-tune (RLCD), few-shot-in-context, balance, OOM knobs, Modal harness, and CPU/MPS/GPU serving.
---

# Laya typed-decision classifier recipe

Laya (https://github.com/NandhaKishorM/laya) is a **non-autoregressive "System-1 decision engine"**: a
ModernBERT-large encoder + an **RL-trained decision head** that makes a typed `choice` / `score` / `noul` in one
forward pass, with per-option **criteria** (written descriptions) and calibrated confidence. It is the **escalation
past SetFit**: use it when the decision — not the representation — is the bottleneck.

**Every number below is a DIRECTION from one project, never a target — re-measure on your own data.**

## When to use Laya (vs SetFit)
- A closed-vocab value **plateaus below the bar with EVERY encoder backbone you try** (we ruled it out with
  LegalBERT, bge-large, all-mpnet, AND ModernBERT-large as SetFit bodies — all stuck). That means the bottleneck is
  the *decision* ("who bears the obligation", "which side is favored", "may either party terminate?"), not the
  embedding. Laya's RL-against-proper-scoring-rules training targets exactly that.
- Measured proof it's a different mechanism: `termination_right` cleared 0.68 with Laya where four encoders maxed at
  0.60–0.64. It is NOT magic — it did NOT rescue minority-data-starved or purely-numeric dims (see Limits).
- Default to SetFit first (cheaper, simpler, CPU-native). Reach for Laya only for the residual confusable/relational
  dims SetFit can't crack.

## Install — uv only, and beware env bleed
- **uv, never pip.** Locally `uv add laya`; on Modal `.uv_pip_install("laya", extra_index_url="https://download.pytorch.org/whl/cu124")` for CUDA torch wheels.
- **The env-bleed gotcha (cost real time):** a bare `uv run --with laya` on a machine with a system Anaconda picked
  up conda's numpy/scipy/sklearn and crashed (`numpy.core.multiarray failed to import`). FIX: force uv's own managed
  Python with a cleared path — `PYTHONPATH= PYTHONNOUSERSITE=1 uv run --python 3.11 --with laya python …` — or run
  inside a proper isolated uv project. Never let a base conda leak in.
- Deps are compatible with our stack: `transformers>=4.48`, `torch>=2.0`, `huggingface_hub`, Python ≥3.10.

## Checkpoints — pick the ENGLISH base, not multilingual
- English base = `convaiinnovations/laya` **root** (`laya.load("convaiinnovations/laya")`, subfolder=None) —
  ModernBERT-large. `subfolder="multilingual"` = mmBERT; `subfolder="typed-decisions"` = a fine-tuned variant.
- **The fine-tune script defaults `--model-dir` to the MULTILINGUAL subfolder** — you must pass the English root
  explicitly, e.g. `snapshot_download("convaiinnovations/laya", allow_patterns=["*.json","*.safetensors","encoder/*","tokenizer/*"])` and point `--model-dir` there. A checkpoint dir = `{encoder/, tokenizer/, model.safetensors, rl_agent_config.json}`.
- Base checkpoints score ~chance zero-shot (~0.36); **all the value is in fine-tuning.** (Zero-shot on our clauses
  was directionally right but ~0.5 confidence — expected.)

## Data — the JSONL schema + criteria (the differentiator)
One JSONL line per case (`research/scripts/finetune_single_device.py` reads this exact shape):
```
{"state": "<clause text>",
 "questions": {"<dim>": {"type":"choice","instructions":"<the question>","criteria":{"<value>":"<description>", ...}}},
 "gold": {"<dim>": {"probabilities": {"<value>": <p>, ...}, "label": "<value>"}}}
```
- **`criteria` = per-value written descriptions = the semantic-guidance lever SetFit never had.** Author them from
  the ontology (`.ttl`); if the ontology is thin (ours had vocab but no per-value defs), author from the value
  semantics and CONFIRM with a human before training. Wording matters — it directly shapes what the model learns.
- **`gold` is a DISTRIBUTION (soft targets), not a hard label** — RLCD trains on the teacher's probability per option.
  One-hot (`{gold:1.0, other:0.0}`) works and is the pragmatic start; true soft targets (teacher per-option probs)
  are the design-intended enhancement.
- Reuse your existing **contract-disjoint, symmetric splits** so floors are directly comparable to SetFit. Silver
  goes in TRAIN only; val/test stay gold.

## Fine-tune — single T4, RLCD, calibration built in
- `python research/scripts/finetune_single_device.py --data <train.jsonl> --model-dir <english-base> --output-dir <out> --epochs 4` (reproduces the 2×T4 notebook without DDP; CPU/one-GPU).
- Runs on **one 16 GB GPU (T4)** — ~1–2 h for large data, minutes for our small dims. RLCD = soft-CE + GRPO-style
  policy gradient on proper scoring rules. Calibration (one temperature per type) is fitted inside the run on a
  held-out slice; **argmax/accuracy unchanged, only confidence moves** — always fit before gating on confidence.
- **OOM knob (hit this):** ModernBERT-large (~400M) OOMs a T4/L4 at the default `batch_size=16, max_seq=256`. Clauses
  are short → set `--batch-size 8 --max-seq 128` (the script/notebook also enables gradient checkpointing). That fit
  comfortably.
- **Modal harness = reuse the [[setfit]] hardened pattern:** `.uv_pip_install("laya")` + `.add_local_file` the
  finetune script; launcher BLOCKS on `.get()` per spawn (no spawn-and-return), stamps + verifies a `data_sha`,
  writes a manifest, streams X/N; snapshot keepers server-side to a `/checkpoints/<name>` path (a small copy fn) —
  the laya volume has no built-in snapshot, add one. Never lose a fine-tune.

## Levers that matter (measured, dim-dependent)
- **Few-shot-in-`state` is a big BUT dim-dependent lever.** Prepend a few labeled exemplars (from TRAIN, per class)
  to the state. It lifted subjective/relational dims a lot (favorability 0.17→0.50, party_asymmetry 0.18→0.55) and
  **HURT a numeric dim** (cap_basis 0.40→0.00, collapsed). **A/B few-shot per dim; never assume it helps.**
- **Balance: don't down-sample to tiny data.** Balancing favorability DOWN to 18/18 (36 rows) + soft targets
  COLLAPSED it (0.00) — the larger imbalanced set did better (0.50). A starved minority value needs balance-**UP**
  (mine more minority data), not down-sampling the majority.
- **The residual failures are minority-data-starvation, not a Laya ceiling** — the closest miss (party_asymmetry
  0.55) is one minority-silver top-up from the bar. Diagnose starvation before concluding "Laya can't."

## Limits (honest)
- Laya rescues *decision-limited* confusable dims; it does **not** fix (a) **minority-data-starved** values (needs
  more data), or (b) **numeric/structural** distinctions (cap_basis "fixed sum vs multiple-of-fees" — encoders and
  Laya both failed; that belongs on the LLM).

## Serving — device-agnostic, load once
- `agent = laya.load(<checkpoint>)` — device auto: CUDA → MPS → CPU. **Verified on a Mac (MPS): ~25 s load, ~1.8 s
  first-call warmup, then ~80–140 ms/call.** Runs on CPU too. So it honors the engine's "use a GPU if present, else
  CPU" philosophy — same as SetFit and the LLM profiles.
- **Load once / preload** (`Router(preload=True)`); never load per call. Serve behind the existing extractor seam so
  nothing upstream changes. The checkpoint is ~820 MB (ModernBERT-large) — heavier than a SetFit body+joblib head;
  budget the memory when co-loading with SetFit models.
- Serving location is NOT pinned: single T4, share the LLM's A100, or CPU/Mac — the seam + a device arg decide at
  runtime, exactly like the model-profile seam for the LLM.

## Reference implementation
`~/work/laya` (the cloned repo: `research/scripts/finetune_single_device.py`, `docs/finetune.md`, `examples/`) and
`~/work/clause-classifier-ab/{laya_modal.py, build_laya_data.py, build_laya_refine.py}` (our Modal fine-tune + eval
+ data builders). Re-use the PATTERNS; the criteria, thresholds, and floors are specific to that problem.
