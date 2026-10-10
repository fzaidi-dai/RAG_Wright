---
name: laya
description: Recipe for fine-tuning and serving a Laya (ModernBERT-large, RL-trained typed-decision) classifier -- the OPEN-weight System-1 decision model -- to replace an LLM decision that a SetFit/encoder classifier PLATEAUS on. Also explains Jev, the MANAGED zero-shot sibling (OpenRouter Decisions API), and when to A/B Jev first (usually) vs fine-tune Laya (on-prem / no managed API). Use when confusable/relational/subjective closed-vocab values won't clear the bar with any encoder backbone. Covers uv install + env-isolation gotchas, the JSONL schema, per-value criteria, single-T4 fine-tune (RLCD), few-shot-in-context, balance, OOM knobs, Modal harness, CPU/MPS/GPU serving, and the jev_decision/DecisionModelProfile wiring (ADR-0119).
---

# Laya typed-decision classifier recipe

Laya (https://github.com/NandhaKishorM/laya) is a **non-autoregressive "System-1 decision engine"**: a
ModernBERT-large encoder + an **RL-trained decision head** that makes a typed `choice` / `score` / `noul` in one
forward pass, with per-option **criteria** (written descriptions) and calibrated confidence. It is the **escalation
past SetFit**: use it when the decision — not the representation — is the bottleneck.

**Every number below is a DIRECTION from one project, never a target — re-measure on your own data.**

## Laya vs Jev (the managed sibling) — pick the decision model first
Laya is the **open-weight** System-1 decision model; **Jev** (TypeSafe, https://openrouter.ai, `typesafe/jev-1.13`
via the OpenRouter **Decisions API**) is the **managed** one — same idea (typed `noul`/`choice`/`score` + calibrated
probabilities), but **strong ZERO/few-shot with no fine-tuning**, whereas Laya scores near-random zero-shot and MUST
be fine-tuned. Measured (RAG_Wright CIC-1c, ADR-0119): on the operative-rule gate **Jev zero-shot hit 0.92** where a
trained SetFit capped ~0.82 and fine-tuned Laya reached ~0.70–0.74. So: **for a closed-set decision, A/B Jev
(zero-shot, instant) first**; reach for Laya when a **managed API is unacceptable** (on-prem / data-residency) and
you can fine-tune. In RAG_Wright a decision model is wired as a `kind="model"` capability (`jev_decision`) behind a
`DecisionModelProfile` (model id / endpoint / thresholds in config) — see `setfit` Phase 0.5 +
`authoring-a-capability`. The decision questions/criteria live in the `.ttl` (ADR-0066), not the capability.

**Swapping Jev for Laya is config, but not free.** `DECISION_PROFILES` has only Jev entries (`jev-1.13`, the
default, and `jev-latest`). A Laya swap needs (1) a server that speaks the Decisions API (POST
`{model, state, questions}`, return `{answers: {<id>: {type, noul|choice|score}}}`), and (2) a `DecisionModelProfile`
entry in `DECISION_PROFILES` with its `endpoint`, `served_model_id` and `api_key_env` (an unknown id falls back to
the OpenRouter endpoint). Then select it with `RAG_DECISION_MODEL=<profile id>`;
`RAG_JEV_TIMEOUT_S` overrides the request timeout.

**Batch the questions.** One call carries one `state` and many keyed questions, so ask everything about a unit in
ONE call (the reference pack judges all of a provision's values, and labels all of its residual candidates, in one
call each). That is the cost shape: one call per unit, not per value.

**Repeatability.** Answers near the threshold can flip across identical calls (Jev: 5/181 flips, all at scores
0.47-0.56; temperature/seed did not help, majority voting barely helped). Fix the ambiguous question instead: state
the rubric once, use structural criteria, pass the items without surrounding text. Re-measure flips on any Laya
server you swap in.

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
- From a clone of the laya repo (its own uv project, not your product's env): `uv run python research/scripts/finetune_single_device.py --data <train.jsonl> --model-dir <english-base> --output-dir <out> --epochs 4` (reproduces the 2×T4 notebook without DDP; CPU/one-GPU; flags: `--data`, `--model-dir`, `--output-dir`, `--device`, `--epochs`, `--seed`). Inside the Modal image the harness runs the copied script with the container's own `python`.
- Runs on **one 16 GB GPU (T4)** — ~1–2 h for large data, minutes for our small dims. RLCD = soft-CE + GRPO-style
  policy gradient on proper scoring rules. Calibration (one temperature per type) is fitted inside the run on a
  held-out slice; **argmax/accuracy unchanged, only confidence moves** — always fit before gating on confidence.
- **Check the fitted temperatures against the SERVING runtime's range (a known Laya mismatch).** The research
  fine-tuning script fits each type's temperature and clips it to [0.1, 10]
  (`research/scripts/finetune_single_device.py:154`); the Laya runtime (0.3.22) serves only [0.5, 5]
  (`laya.common.TEMP_MIN` / `TEMP_MAX`) and clamps the rest, with a load-time `RuntimeWarning` ("... values outside
  [0.5, 5] ... treat confidence ... as uncalibrated"). A clamped `choice` temperature leaves the ranking alone (one
  scale per question: argmax, top-k and `none`-abstains are stable) but makes the probabilities sharper than
  fitted, so they are overconfident. So, after every training run:
  1. read `temperature` in each checkpoint's `rl_agent_config.json` and compare it with the runtime range;
  2. if you only rank (top-k, abstain on `none`), an out-of-range value changes no label: record it and move on;
  3. if anything gates on `probabilities` or `answer_confidence`, refit within the runtime range with the runtime
     calibrator on a held-out labelled slice (`laya.calibrate.records_from_labeled(agent, pairs)` ->
     `fit_temperature_map(records)`, whose fits are clamped to the runtime range), save it with
     `Agent.save_calibration(path)` and serve with `Agent.load_calibration(path)`.

  The engine's reference fleet has nine such checkpoints and only ranks (ADR-0129). A report to Laya upstream is
  drafted in the engine repository (`docs/upstream/laya-temperature-range-mismatch.md`, not yet filed); check the
  Laya release notes before relying on either range. Repository paths in this skill (`docs/`) are in the engine repository:
  read them there or on GitHub.
- **Train with the scikit-learn your serving environment runs** (or raise the serving floor with it): a SetFit
  head pickled by a newer scikit-learn warns on load and can change predictions without an error.
- **OOM knobs:** ModernBERT-large (~400M) is tight on a 16 GB T4/L4. The single-device script has NO batch or
  sequence flags: it fixes `micro_batch = 8`, `max_tokens_per_batch = 4096`, `max_len = 1024` and `head_max_len = 256`
  in code, and turns on gradient checkpointing on CUDA. Our short-clause dims ran with it unchanged. If a run still
  OOMs, lower those values in a copy of the script, or use the repo's `laya_finetune_typed_decisions_mps.py` (under notebooks/), which
  takes `--micro-batch` and `--grad-accum`.
- **Run preflight FIRST — see the [[setfit]] "Run preflight & monitoring" section.** It is framework-agnostic and
  applies to Laya exactly as to SetFit, including when you GENERATE the Laya JSONL labels with a teacher: resolve
  the model from the engine (never a hardcoded/stale id; bulk teacher labeling on Modal Qwen, not OpenRouter), load
  `.env` by EXPLICIT path from an out-of-repo script, SMOKE one item before the fan-out, and stream X/N to a log you
  actively monitor. Those exact mistakes cost runs on 2026-10-04.
- **Modal harness = reuse the [[setfit]] hardened pattern:** `.uv_pip_install("laya")` + `.add_local_file` the
  finetune script; launcher BLOCKS on `.get()` per spawn (no spawn-and-return), stamps + verifies a `data_sha`,
  writes a manifest, streams X/N; snapshot keepers server-side to a `/checkpoints/<name>` path (a small copy fn) —
  the laya volume has no built-in snapshot, add one. Never lose a fine-tune.
- **ACCOUNT CONTAINER CAP = 10 (fzaidi2014).** Spawning more than 10 fine-tunes at once does NOT run them all —
  Modal queues the rest and runs ~10 at a time (correct, but ~N/10 waves of wall-clock, and a "why only 10 running?"
  surprise). Size a `groupbake`/dimbatch fan-out to ≤10 in flight (chunk into waves + gather between), or state the
  wave count honestly. This is a DIFFERENT knob from the vLLM `max_containers=1` + `@modal.concurrent` batching in the
  qwen-vllm-modal skill — do not conflate. (Ignored the stated cap once, spawned 29 → 3 waves.)

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
The laya repo (its `research/scripts/finetune_single_device.py`, its fine-tuning guide and its `examples/`) and
the engine authors' own Modal fine-tune + eval + data builders (a private working repo, not shipped). Re-use the PATTERNS; the criteria, thresholds, and floors are specific to that problem.
