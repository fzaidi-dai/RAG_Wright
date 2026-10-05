---
name: qwen-vllm-modal
description: Authoritative recipe for standing up the self-hosted Qwen3.8-27B vLLM server on Modal (the production LLM substrate). READ THIS + ADR-0110 before touching the deploy — it fixes the "we forgot the agreed config and rediscovered every vLLM/Modal gotcha the hard way" failure. Covers the locked config, the image-build fixes, the concurrency + timeout serving fixes, cold start, and cost control.
---

# Qwen3.8-27B on Modal (vLLM) — the production LLM server

**Read the record, do NOT reconstruct from memory.** The config is decided and the gotchas are known. Rummaging
half-remembered fragments and iterating live cost a full painful session even though it had all been done, decided,
and tested before. The two sources of truth:
1. **ADR-0110** (`docs/adr/0110-fp8-kv-cache-16k-single-a100.md`) — the LOCKED config + why.
2. **`scripts/modal_qwen3_vllm_server.py`** — the deploy script.
This skill is the operational checklist that ties them together.

## The LOCKED production config (ADR-0110) — do not re-litigate
**Config B: `Qwen/Qwen3.8-27B-FP8` weights + `--kv-cache-dtype fp8`, on 1× A100-80GB, TP=1, `--max-model-len 16384`, `--gpu-memory-utilization 0.95`.**
- Config B (FP8 weights) is the **locked deploy** — 52× concurrency @16K, ~23% higher throughput, ~27 GB weights;
  accuracy measured **identical to bf16** on the real compliance workload (no FP8 penalty).
- **Config A** (`Qwen/Qwen3.8-27B`, **bf16 weights** + FP8 KV) is the **documented FALLBACK only** — 25× concurrency,
  "zero weight-loss" — use it *only* if a later clean check ever shows an FP8-weight regression. Don't default to A.
- If anyone (including a future you) says "config A is production" — **check ADR-0110 first**; the ADR locks **B**.
- Served-model-name is `Qwen/Qwen3.8-27B` (what the engine profile `qwen3.8-27b-modal` asks for), even though the
  weights are the `-FP8` repo.

**The exact working deploy (this succeeded):**
```
MODAL_IMAGE_BUILDER_VERSION=2025.06 \
  APP_NAME=rw-qwen3-modal MODEL=Qwen/Qwen3.8-27B-FP8 KV_CACHE_DTYPE=fp8 \
  SERVED_NAME=Qwen/Qwen3.8-27B TOOL_PARSER=hermes REASONING_PARSER=qwen3 \
  GPU=A100-80GB:1 TP=1 MAX_LEN=16384 GPU_UTIL=0.95 MAX_NUM_SEQS=<target> \
  uv run --no-sync modal deploy scripts/modal_qwen3_vllm_server.py
```
Then wire the engine: `.env` `VLLM_BASE_URL=https://<workspace>--rw-qwen3-modal-serve.modal.run/v1`,
`VLLM_API_KEY=rw-vllm-dev-key`; the profile `qwen3.8-27b-modal` routes there. **Stop billing when done:**
`uv run --no-sync modal app stop rw-qwen3-modal --yes` (A100 is expensive).

## Image-build gotchas (the painful iterations — all fixed)
Building a fresh vLLM image on a clean Modal account exposed a chain of failures. **The reliable fix is to base off
the OFFICIAL prebuilt vLLM image** rather than pip-building vLLM:
1. `pip_install("vllm")` (unpinned) on `cuda:12.8.1` → **`xformers` source-builds** → `ModuleNotFound: torch`
   (pip build-isolation). Don't pip-build vLLM.
2. `uv_pip_install("vllm", extra_index_url=pytorch-cu)` → resolver "no versions of vllm … unsatisfiable" — also fragile.
3. **WORKING:** `modal.Image.from_registry("vllm/vllm-openai:latest")` — vllm+torch+xformers+flashinfer prebuilt,
   nothing compiles. It needs two adjustments:
   - `setup_dockerfile_commands=["RUN ln -sf $(command -v python3) /usr/local/bin/python"]` — the image ships
     `python3` but not `python`; Modal's **own pip bootstrap runs right after FROM** as `python -m pip` → **exit 127**
     without the symlink. It must be in `setup_dockerfile_commands` (runs BEFORE the bootstrap), not a later layer.
   - `.entrypoint([])` — clear the image's ENTRYPOINT (the api_server) so Modal's runtime isn't hijacked.
4. **Modal LEGACY image builder clobbers the stack**: it installs its old client deps OVER the vLLM image,
   downgrading **pydantic→v1 and fastapi→old**, so vLLM crashes at startup (`cannot import name 'model_validator'`,
   then `cannot import name 'Undefined' from pydantic.fields`). Patching pydantic just moves the break to fastapi
   (whack-a-mole). **FIX = `MODAL_IMAGE_BUILDER_VERSION=2025.06`** (a modern builder whose client stack is pydantic-v2
   compatible). Valid builder versions: `{2023.12, 2024.04, 2024.10, 2025.06}` — use the newest.
5. `HF_HUB_DISABLE_XET=1` in the image env — Xet holds an open log handle under the HF cache so `vol.commit()` fails
   on a fresh weight download.

## Serving gotchas (equally painful — all fixed)
1. **`@modal.concurrent(max_inputs=N)` is REQUIRED on the web_server function.** Without it Modal feeds the single
   container ONE request at a time (`Running: 1 reqs` + a flood of "Received a cancellation signal", `/health`
   blocked) — vLLM's continuous batching is starved. Concurrency comes from **KV-cache batching inside ONE container**
   (`max_containers=1`), not from more containers. Set `max_inputs` = your `--max-num-seqs`.
2. **The client structured-call timeout is too tight for reasoning-ON calls under batch.** At `--max-num-seqs` load,
   reasoning calls take ~50–60 s; the seam's default 60 s timeout kills the ones just over the line → **retry pileup →
   requests pile past `max_inputs` → instant `APIConnectionError` (0.0 s) cascade**. FIX: the seam reads
   `RAG_STRUCTURED_TIMEOUT_S` (default 60) — raise it (e.g. `150`) for reasoning-ON bulk work. Server logs confirm the
   calls actually complete 200 OK (~50 s); it was the client giving up.
3. **Pick client concurrency empirically, below the ceiling.** 16 was stable; 20 (== `max-num-seqs`) sat at the exact
   ceiling and risked queueing. `Running: N, Waiting: 0` + zero cancellations = healthy.
4. **Reasoning ON vs OFF for bulk classification:** reasoning-OFF is ~30× faster but **changes the labels**
   (over-keeps / different distribution) — do NOT swap it in for work that must match the reasoning-teacher's gold
   (e.g. distillation silver). Keep reasoning ON when fidelity to the gold teacher matters; accept the cost.

## Cold start & cost
- Cold start ~440–480 s (CPU-bound engine init + a 51-graph CUDA-graph capture + first-time weight download), NOT
  weight I/O — no load trick alone fixes it. GPU-snapshot / sleep-mode is a DEFERRED todo (the one attempt failed
  because it was CPU-only; GPU retry not done). Run long deploys detached; poll `/health` for readiness.
- **A100 is billed while up — `modal app stop <app> --yes` the moment you're done.** Verify with `modal app list`
  (state `stopped`, 0 tasks) + endpoint 404.
- **Do NOT warm the GPU before the consumer is ready, and do NOT trust auto-scaledown to protect billing** (burned
  2026-10-04: a smoke-warmed A100 sat idle ~20 min while unrelated work ran, because "scaledown_window will handle
  it" was assumed — it did not, fast enough). RULE: build the bulk/label/eval consumer FIRST (while the app is
  stopped/cold), THEN warm → smoke → run → **`modal app stop` immediately after**, in one continuous go. If you warm
  only to prove stand-up and the consumer is not next, STOP it right after the smoke. Treat the explicit stop as
  mandatory, not the scaledown as sufficient; verify stopped with `modal app list`.

## The meta-lesson
Config + recipe live in **ADR-0110 + the script + this skill**. Before any Qwen/Modal work: read them, don't
reconstruct. When something "was working and we changed accounts/rebuilt," expect the image-builder + concurrency +
timeout trio above — they are the recurring three.

## Client side (when you use this server for bulk labeling/eval)
The SERVER config is here; the CLIENT preflight (resolve the model from the engine not a hardcoded id, load `.env`
by explicit path from an out-of-repo script, smoke ONE item before the bulk fan-out, mandatory X/N progress +
active monitoring) is in the **`setfit` skill's "Run preflight & monitoring"** section. Read it before driving a
bulk job against this server — those client gotchas (a stale model id, a silently-unloaded `.env`) cost a run each
on 2026-10-04.
