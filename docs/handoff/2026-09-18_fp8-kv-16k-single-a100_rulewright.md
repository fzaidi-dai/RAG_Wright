# RuleWright handoff: 16K context AND ≥20 concurrency on ONE A100 — FP8 KV cache

Date: 2026-09-18 · on `origin/main` · ADR-0110 · **Ops/deploy finding. Serves your real 16K workload on a single A100-80GB at 25–53× concurrency. No engine code change.**

---

## The headline

You do **not** need 8K, and you do **not** need two GPUs. Adding **`--kv-cache-dtype fp8`** lets `Qwen3.8-27B` serve full **16384** context on one A100-80GB at **≥20 concurrent requests** with negligible accuracy loss. Measured, TP=1, 16384, util 0.95, **0 errors**:

| config | weights | Available KV | KV tokens | **max concurrency @16K** | req/s @64 | out-tok/s @64 | TTFT p50 @64 | warm cold-start |
|---|---|---|---|---|---|---|---|---|
| **A — bf16 wt + FP8 KV** | bf16 ~54 GB | 20.76 GiB | 417,310 | **25.47×** | 5.21 | 668 | 1.30s | 439s |
| **B — FP8 wt + FP8 KV** | FP8 ~27 GB | 42.9 GiB | 863,533 | **52.71×** | 6.43 | 823 | 1.95s | 481s |
| (ref) bf16 + bf16 KV | bf16 ~54 GB | ~17 GiB | 217,239 | 13.26× (under target) | 5.36 | 686 | 1.39s | 481s |

## Which to run

- **A (`Qwen/Qwen3.8-27B` + FP8 KV)** — the **zero-weight-loss** option: weights stay bit-for-bit bf16; the only approximation is FP8 KV cache (sub-point, long-context-robust per vLLM's own study). 25× concurrency. Use this if you want the safest possible accuracy story.
- **B (`Qwen/Qwen3.8-27B-FP8` + FP8 KV)** — official FP8 weights ("near-identical" accuracy). On this A100 it measured **~23% higher throughput and lower total latency** than A (smaller weights → faster decode), at ~0.65s higher TTFT. 53× concurrency, and ~27 GB weights, which is why it's also the better base for the upcoming fast-cold-start (snapshot) work.

## Exact serve flags

```
# Config A (zero weight loss)
vllm serve Qwen/Qwen3.8-27B      --tensor-parallel-size 1 --max-model-len 16384 \
  --gpu-memory-utilization 0.95 --kv-cache-dtype fp8 --max-num-seqs <your target> --dtype bfloat16 --trust-remote-code
# Config B (more headroom + throughput): swap the model
vllm serve Qwen/Qwen3.8-27B-FP8  --tensor-parallel-size 1 --max-model-len 16384 \
  --gpu-memory-utilization 0.95 --kv-cache-dtype fp8 --max-num-seqs <your target> --dtype bfloat16 --trust-remote-code
```

## Before you trust it in production — one accuracy check

These are **performance/concurrency** numbers, not a task-accuracy eval. Run a few hundred of your real 9K+ prompts and compare bf16-KV vs FP8-KV (and A vs B) on your task metric. Expect sub-point. If you see a persistent downward shift, bake calibrated KV scales (llm-compressor) or set `calculate_kv_scales=True` — FP8 KV defaults to an uncalibrated scale of 1.0.

## Not solved here

Cold start is still ~440–480s (FP8 shrinks memory, not the CPU-bound engine-init + CUDA-graph capture). Getting restart under a minute is the separate snapshot work (ADR-0109); FP8 weights (Config B) directly help it by halving the sleep-mode CPU-RAM and snapshot size.

Reference: ADR-0110, ADR-0108 (single-A100 profile), ADR-0109 (cold-start), `scripts/modal_qwen3_27b_bench.py` (`KV_CACHE_DTYPE` knob). FP8 KV verified on A100 (`sm80`).
