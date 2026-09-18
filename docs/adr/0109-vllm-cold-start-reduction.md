# ADR-0109: cutting Qwen3.8-27B cold start toward sub-60s — the reframe, and why the Modal GPU snapshot path is newly viable

**Status:** accepted (direction) · **Date:** 2026-09-16 · **Related:** ADR-0108 (single-A100 serving profile), ADR-0039 (product substrate: self-hosted vLLM on Modal/A100) · **Prototype: pending approval**

## Context

Qwen3.8-27B (full bf16, ~54 GB weights) cold-starts to a healthy `vllm serve` in **~481s even with the torch.compile cache persisted on a Volume and reused** (ADR-0108). We want cold restart **under 60s**. A prior attempt at Modal's memory-snapshot feature did **not** work for us, so it was assumed off the table. This ADR records what the cold start actually consists of, why the snapshot approach failed then, what changed, and the chosen direction.

## The reframe: the 481s is predominantly CPU-bound engine init, not weight I/O

Startup decomposes into: weight load, torch.compile (already cached, no longer dominant), CUDA-graph capture, and engine init / profiling / warmup / NCCL. The literature (MLSys 2026 "Breaking the Ice: Analyzing Cold Start Latency in vLLM", arxiv 2606.07362; a 35B breakdown at route179.dev) finds vLLM startup **predominantly CPU-bound**. For our case:

- **Weight load**: Modal Volumes deliver ~1–2 GB/s, and the default safetensors reader is single-stream. 54 GB ⇒ **~27–54s** best case, worse in practice.
- **CUDA-graph capture**: ~67 graphs by default, "> 10s," scaling with model size ⇒ **~15–30s** for a 27B.
- **Engine init / profiling / warmup / NCCL**: the large CPU-bound remainder — the likely reason we sit at 481s+.

**Consequence, and the load-bearing conclusion: no weight-loading optimization alone reaches sub-60s.** Even driving the 54 GB load to zero leaves the CPU-bound init + capture over budget. Sub-60s requires attacking the *whole* startup (snapshot or keep-warm), not just I/O.

## Why the Modal snapshot did NOT work before, and what changed

Modal's memory-snapshot feature has two generations, and the difference is exactly what breaks a GPU inference server:

- **When we tried it — CPU-only memory snapshot.** It captured host/CPU process state and RAM only; it did **not** capture GPU VRAM, the CUDA context, CUDA streams, or captured CUDA graphs (the original model even snapshotted *before a GPU was attached*). vLLM's real state is GPU-resident: 54 GB of weights in VRAM, a CUDA context, captured CUDA graphs. Snapshotting the CPU side of a process whose state lives on the GPU means that on restore the CUDA context/VRAM is gone or invalid ⇒ vLLM crashes on restore or must re-init the GPU anyway, defeating the purpose. The feature was **structurally incapable** of capturing the state that matters here. (Diagnosis inferred from the timeline + Modal's documented CPU-only limitation; we do not have the exact error text from the prior attempt recorded.)
- **What changed (July 30, 2025, alpha): Modal added GPU memory snapshots.** These now capture **GPU VRAM contents (weights on-device), CUDA kernels, CUDA streams and contexts, and device address mappings** — precisely the state the old feature could not. Enabled via `experimental_options={"enable_gpu_snapshot": True}` + `@modal.enter(snap=True)`. Modal also published a **sanctioned vLLM recipe** (LFM2-24B example) pairing this with vLLM **sleep mode** (`--enable-sleep-mode`): warm the server, `/sleep` in the snap-enter, `/wake_up` in the restore-enter. Sleep-mode level 1 parks the 54 GB in **CPU RAM** so the weights land *in the snapshot* rather than being re-streamed from the Volume, converting the weight-load bucket into a snapshot-restore + host→device copy. Reported speedups 2–10× (e.g. Qwen2.5-0.5B 45s→5s), stabilizing after < 5 warm boots.

**So the effort is now integration, not invention.** Previously there was no mechanism to capture GPU/CUDA state at all (impossible). Now the hard part is handled by the platform + vLLM sleep mode; our work is: run **TP=1** (already validated viable by ADR-0108, and *mandatory* because snapshots are multi-GPU-incompatible), wire the sleep-mode lifecycle, set the snapshot flags, and build the snapshot over a few warm boots.

## Decision

**Rule out "keep one warm" for now** (`min_containers=1`): it is the only *guaranteed* sub-60s answer today, but it costs one A100-80GB idle 24/7, which is not justified pre-customers. Revisit once customer load justifies a standing replica.

**Pursue the genuinely-fast-cold-start path: TP=1 + vLLM sleep mode + Modal GPU memory snapshot**, layered on the supporting weight-load/capture trims. Concretely, the intended stack:

1. **TP=1** (ADR-0108) — mandatory for snapshots and removes NCCL/distributed init cost.
2. **vLLM sleep mode level 1** (`--enable-sleep-mode`, `VLLM_SERVER_DEV_MODE=1`) — weights resident in CPU RAM (~54 GB host RAM required), captured in the snapshot; wake ~3–6s for large models.
3. **Modal GPU memory snapshot** (`experimental_options={"enable_gpu_snapshot": True}`, `@modal.enter(snap=True)` warm+`/sleep`, `@modal.enter(snap=False)` `/wake_up`), with `TORCHINDUCTOR_COMPILE_THREADS=1` (torch.compile can otherwise make snapshot creation fail).
4. **Supporting trims** (lower the baseline the snapshot-build and any non-snapshot cold start pay): `--load-format runai_streamer` (~2–2.4× on the load bucket, one flag) or CoreWeave Tensorizer (single-blob, fastest measured); keep `VLLM_CACHE_ROOT` on a persistent Volume (already done); trim `cudagraph_capture_sizes` to the batch sizes actually served.

Plausible target after the snapshot is built: **~10–20s cold restart**, well under 60s, at far lower steady-state cost than a pinned GPU.

## Consequences

- **Risk is alpha-feature risk.** Modal's GPU snapshot is alpha ("test carefully before production"). The medium-effort estimate assumes documented behavior. The decisive, low-cost way to settle whether it works *now* is a prototype against `scripts/modal_qwen3_27b_bench.py` measuring real restore time — **pending approval; not started.**
- **Hard constraints to respect:** multi-GPU is unsupported (TP=1 only); ~54 GB CPU RAM needed for sleep level 1; TP=1 memory headroom (54 GB weights on 80 GB leaves ~26 GB for KV — the 8192/0.95 config from ADR-0108 fits); `TORCHINDUCTOR_COMPILE_THREADS=1` workaround for snapshot creation.
- **Fallback if the alpha proves unstable:** the §4 supporting trims alone do NOT reach sub-60s (per the reframe), so the fallback for a hard sub-60s SLA reverts to keep-one-warm (the ruled-out cost) until the snapshot path matures.
- No engine code changes here; this is deployment/serving infrastructure for the product substrate (ADR-0039). This ADR sets direction and diagnosis; a follow-up will record the prototype's measured result.

### Sources
- Modal GPU snapshots (alpha, what's captured): https://modal.com/blog/gpu-mem-snapshots ; caveats (multi-GPU incompat, storage-bound caveat, torch.compile): https://modal.com/docs/guide/memory-snapshots ; vLLM example: https://modal.com/docs/examples/lfm_snapshot , https://modal.com/docs/examples/gpu_snapshot
- Cold-start is CPU-bound; Tensorizer fastest: https://arxiv.org/abs/2606.07362 ; 35B breakdown: https://route179.dev/2026/06/30/optimizing-vllm-cold-start-with-model-streaming-and-compile-caching/
- vLLM sleep mode: https://vllm-project.github.io/2025/10/26/sleep-mode.html , https://docs.vllm.ai/en/latest/features/sleep_mode/
- vLLM loaders: runai_streamer https://docs.vllm.ai/en/stable/models/extensions/runai_model_streamer/ ; Tensorizer https://docs.vllm.ai/en/v0.10.1/models/extensions/tensorizer.html
- vLLM CUDA graphs / capture cost: https://docs.vllm.ai/en/stable/design/cuda_graphs/
- Modal Volume 1–2 GB/s, keep-warm knobs: https://modal.com/docs/guide/high-performance-llm-inference , https://modal.com/docs/guide/cold-start , https://modal.com/docs/guide/scale
