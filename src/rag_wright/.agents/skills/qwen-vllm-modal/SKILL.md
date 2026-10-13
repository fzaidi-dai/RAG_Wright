---
name: qwen-vllm-modal
description: Authoritative recipe for standing up the self-hosted Qwen3.8-27B vLLM server on Modal (the production LLM substrate). READ THIS + ADR-0110 before touching the deploy — it fixes the "we forgot the agreed config and rediscovered every vLLM/Modal gotcha the hard way" failure. Covers the locked config, the image-build fixes, the concurrency + timeout serving fixes, cold start, and cost control.
---

# Qwen3.8-27B on Modal (vLLM) — the production LLM server

**Read the record, do NOT reconstruct from memory.** The config is decided and the gotchas are known. Rummaging
half-remembered fragments and iterating live cost a full painful session even though it had all been done, decided,
and tested before. The two sources of truth:
1. **ADR-0110** (`docs/adr/0110-fp8-kv-cache-16k-single-a100.md`) — the LOCKED config + why.
2. **`scripts/modal_qwen3_vllm_server.py`** — the deploy script. It ships with this skill, in this skill's own
   `scripts/` directory, so a product deploys it from there (it needs only `modal` in your environment); in the
   engine repo `scripts/modal_qwen3_vllm_server.py` links to the same file.
ADR-0110 and the other `docs/` paths are in the engine repository: read them there or on GitHub, at the tag
matching your installed engine.
This skill is the operational checklist that ties them together.

**Which route.** A product deploys and reaches the server through the engine API, `adeploy_model_server` (the
section after the locked config). The raw `modal deploy` of the bundled script further down is the engine
developer's route, and what the API runs underneath. **When to stop it** is one rule, in "Cold start & cost": deploy
once, let it scale to zero between uses, never stop it between runs.

## The LOCKED production config (ADR-0110) — do not re-litigate
**Config B: `Qwen/Qwen3.8-27B-FP8` weights + `--kv-cache-dtype fp8`, on 1× A100-80GB, TP=1, `--max-model-len 16384`, `--gpu-memory-utilization 0.95`.**
- Config B (FP8 weights) is the **locked deploy** — 52.71× concurrency @16K (ADR-0110 rounds it to 53×), ~23% higher throughput, ~31 GB weights (30.9 GB on disk, 28.9 GiB in VRAM);
  accuracy measured **identical to bf16** on the real compliance workload (no FP8 penalty).
- **Config A** (`Qwen/Qwen3.8-27B`, **bf16 weights** + FP8 KV) is the **documented FALLBACK only** — 25.47× concurrency,
  "zero weight-loss" — use it *only* if a later clean check ever shows an FP8-weight regression. Don't default to A.
- If anyone (including a future you) says "config A is production" — **check ADR-0110 first**; the ADR locks **B**.
- Served-model-name is `Qwen/Qwen3.8-27B` (what the engine profile `qwen3.8-27b-modal` asks for), even though the
  weights are the `-FP8` repo.

**From the engine (PS-19): deploy, then use the server like any model.** `ModelServerSpec`'s defaults ARE the
locked config below (FP8 weights + FP8 KV, one A100-80GB, TP 1, 16K, 0.95, served `Qwen/Qwen3.8-27B`, hermes +
qwen3 parsers, image builder 2025.06); state only the concurrency target, which ADR-0110 leaves to you.
`adeploy_model_server` runs the deploy below with those settings, waits for `/health` (with progress; a restore from
the snapshot is seconds, a snapshot-building boot 4-9 min, see Cold start below), and registers a model profile with the server's own endpoint, so no `VLLM_BASE_URL` is needed:

```python
from rag_wright.api import EngineConfig, ModelServerSpec, adeploy_model_server, stop_model_server

server = await adeploy_model_server(ModelServerSpec(name="acme-qwen", max_num_seqs=32))  # key from VLLM_API_KEY
config = EngineConfig(store=..., models={"general": server.model_id})   # or any model= argument
...
stop_model_server("acme-qwen")  # only to retire it: stopping discards the snapshots (see "Cold start & cost")
```

`server.model_id` is `qwen3.8-27b-modal@<name>`: a copy of the `qwen3.8-27b-modal` profile pointed at this server
(reasoning on for structured calls and agent turns, off for free text). Use that id, never `VLLM_BASE_URL`. A
reasoning-off structured pick (bulk closed-vocab labeling) has no `@<name>` variant: point `VLLM_BASE_URL` at
`server.base_url` and use the `qwen3.8-27b-modal-nothink` profile.
`amodel_server(spec)` reconnects to a server deployed earlier (a restart, another process) without redeploying;
`await_model_server(server, timeout_s=...)` waits for readiness; `amodel_server_status(spec)` looks without waking it
(`deployed`, `running` (a container bills), `healthy`, `weights_cached`); `astop_model_server(name)` is the async stop.
The API key comes from the variable `ModelServerSpec.api_key_env` names (default `VLLM_API_KEY`); there is no default
key, so set a real one. Needs `rag-wright[modal]` and a Modal login. One app per server name: different names give
different servers (and URLs), for example one per tenant.

**The engine developer's route: the raw deploy (this succeeded):**
```
VLLM_API_KEY=<a long random string> MODAL_IMAGE_BUILDER_VERSION=2025.06 \
  APP_NAME=rw-qwen3-modal MODEL=Qwen/Qwen3.8-27B-FP8 KV_CACHE_DTYPE=fp8 \
  SERVED_NAME=Qwen/Qwen3.8-27B TOOL_PARSER=hermes REASONING_PARSER=qwen3 \
  GPU=A100-80GB:1 TP=1 MAX_LEN=16384 GPU_UTIL=0.95 MAX_NUM_SEQS=<target> \
  uv run --no-sync modal deploy scripts/modal_qwen3_vllm_server.py
```
(From a product repo, deploy the copy in this skill's directory: `.claude/skills/qwen-vllm-modal/scripts/modal_qwen3_vllm_server.py` once the skills are linked.)
The script refuses to deploy without `VLLM_API_KEY`: Modal's web endpoint is public and unauthenticated, so vLLM's
`--api-key` is the only gate on a billing A100, and there is no default key. Then wire the engine: `.env`
`VLLM_BASE_URL=https://<workspace>--rw-qwen3-modal-serve.modal.run/v1` and the same `VLLM_API_KEY`; the profile
`qwen3.8-27b-modal` routes there (the seam also refuses an unset key). When to stop it: "Cold start & cost" below.

**The engine does NOT reach Modal by default.** Every role's default is `_PRODUCT_LLM = "qwen3.8-27b-modal-or"`,
the same model routed through OpenRouter (a Modal cold start can still take minutes when Modal builds a snapshot). To send calls to the Modal server, put
`server.model_id` in `EngineConfig.models` (the API route), or on the raw route set `RAG_MODEL_ALL=qwen3.8-27b-modal`
(all roles) or a per-role `RAG_MODEL_<ROLE>`. For bulk
closed-vocab labeling, the reasoning-off profile `qwen3.8-27b-modal-nothink` exists (opt-in; see the reasoning
caveat below). `VISION_OCR` now defaults to the product LLM too, but image inputs through the Modal vLLM server are
UNVERIFIED (the serve script passes no multimodal flags): confirm an image call works before pointing
`RAG_MODEL_VISION_OCR` at Modal.

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
   calls actually complete 200 OK (~50 s); it was the client giving up. The total per call (all tries) follows it;
   `RAG_MODEL_DEADLINE_S` overrides that (ADR-0133). Reasoning loops end at the profile's 8,192-token structured cap,
   not at the deadline; keep temperature 0 (Qwen's recommended sampling did not remove them).
3. **Pick client concurrency empirically, below the ceiling.** 16 was stable; 20 (== `max-num-seqs`) sat at the exact
   ceiling and risked queueing. `Running: N, Waiting: 0` + zero cancellations = healthy.
4. **Reasoning ON vs OFF for bulk classification:** reasoning-OFF is ~30× faster but **changes the labels**
   (over-keeps / different distribution) — do NOT swap it in for work that must match the reasoning-teacher's gold
   (e.g. distillation silver). Keep reasoning ON when fidelity to the gold teacher matters; accept the cost.
5. **Never expose vLLM's port directly (PS-20).** Sleep mode needs `VLLM_SERVER_DEV_MODE=1`, which adds
   unauthenticated `/sleep`, `/wake_up` and `/collective_rpc` routes (`--api-key` guards only `/v1`, `/v2`,
   `/inference`, `/cohere`). The script binds vLLM to 127.0.0.1:8001 and serves `rw_gate.py` on the public port,
   forwarding only `/v1/*` and `/health`; keep `rw_gate.py` next to the script.

## Cold start & cost
- **The server is a class restored from a Modal GPU memory snapshot (PS-20, ADR-0132).** A boot without a snapshot
  starts vLLM with `--enable-sleep-mode`, warms it, sleeps it at level 1 (weights to host RAM, KV discarded) and
  Modal snapshots the container; a restore only wakes vLLM (~3.5 s) and is ready ~13 s after its container starts.
  Modal builds the first few snapshots after a deploy (4-9 min boots; 3-4 in our runs) and now and then another
  (a new host, a preemption): budget for an occasional slow cold start. Redeploying keeps the snapshots; **`modal app
  stop` discards them**. Snapshots need TP=1 (the script boots without one at TP>1).
- Without a snapshot a cold start is ~2-4 min on Config B (114-245 s measured): about 30 s of imports in EACH of
  vLLM's two processes (transformers' `importlib.metadata.packages_distributions()` stats every installed file),
  a weight read of 12-154 s from the Volume (9P), then compile-cache load + CUDA-graph capture. Measured and NOT
  worth adopting (ADR-0109 addendum): `--load-format runai_streamer`, precompiled `.pyc`, `HF_HUB_OFFLINE=1` (it
  changes vLLM's compile-cache key every container, so each start recompiles). Poll `/health` for readiness.
- **When to stop it -- one rule (EV-6; it used to contradict the line above).** The A100 is billed only while a
  container runs; the server scales to zero after 10 idle minutes (`scaledown_window=600`, no warm minimum) and then
  costs nothing and KEEPS its snapshots, so the next request is back in ~13 s. `modal app stop` saves at most those
  10 minutes and makes the next deploy pay a snapshot-building boot (usually 4-9 min; 15 min seen once) (2026-10-12: a stop after a failed smoke
  run, then a redeploy, cost a 911 s boot).
  1. **Deploy once per server name**; reuse the name (a new name builds new snapshots).
  2. **Build the consumer first** (the bulk / label / eval script, its smoke test, its progress logging) while no
     container runs, then warm, smoke and run in one go. Do not warm a GPU before its consumer is ready (2026-10-04:
     a smoke-warmed A100 sat idle ~20 min while unrelated work ran).
  3. **Between runs, and when you are done, let it scale to zero.** Never stop it between runs; a script's error
     path must not stop it either when a fix-and-rerun is likely.
  4. **Check it reached zero**: `amodel_server_status(spec).running` is False (or `modal app list` shows the app
     `deployed` with 0 tasks) about 10 minutes after the last call. If tasks remain past the window, find out why (that is the 2026-10-04 burn) and stop it.
  5. **Stop (`modal app stop <app> --yes`, or `stop_model_server`) only to retire a server**: you will not use it
     for a long time and do not want its endpoint up, or you are replacing its deployment. Expect the next deploy to
     rebuild the snapshots.

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
