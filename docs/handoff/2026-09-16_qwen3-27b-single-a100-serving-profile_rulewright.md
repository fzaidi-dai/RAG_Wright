# RuleWright handoff: Qwen3.8-27B runs on ONE A100-80GB — how to configure it (benchmark)

Date: 2026-09-16 · on `origin/main` · ADR-0108 · **Ops/deploy finding, no engine code change. Halves the GPU cost of serving Qwen3.8-27B vs the 2×A100 baseline.**

---

## The headline

**Qwen3.8-27B (full bf16) serves comfortably on a single A100-80GB.** You do not need two GPUs for this model at the clause/compliance workload. Deploy with `TP=1`, `gpu_memory_utilization=0.95`, and the **smallest `max_model_len` your prompts actually need** — that last knob is your concurrency lever.

Measured on 1× A100-80GB, production-faithful served path (`vllm serve`, torch.compile ON, CUDA graphs, continuous batching), clause→JSON workload, 256 output tokens/request:

| config | KV cache | KV tokens | **max concurrency** | req/s @64 | out-tok/s @64 | TTFT p50 @64 | errors |
|---|---|---|---|---|---|---|---|
| util 0.95, max_len **8192** | 21.09 GiB | 212,028 | **25.88×** | 5.33 | 683 | 1.27s | 0 |
| util 0.90, max_len 16384 | 17.13 GiB | 217,239 | 13.26× | 5.36 | 686 | 1.39s | 0 |

Full sweep at 0.95/8192: C=1→64 → 0.22→5.33 req/s, 28→683 out-tok/s, TTFT p50 0.12→1.27s, **0 errors at every level**.

## Three things to know before you deploy on 1 GPU

1. **A 2-GPU config does NOT "just work" on 1 GPU.** vLLM hard-fails if `--tensor-parallel-size` exceeds the visible GPU count (`World size (2) is larger than the number of available GPUs (1)`) — it does not adapt down. **Set `TP` to the GPU count** (one edit). Everything else (KV sizing, concurrency) is then automatic.

2. **`max_model_len` is the concurrency lever, not `gpu_memory_utilization`.** The physical KV budget on one 80GB card is ~212–217k tokens regardless of these settings. Concurrency = `KV_tokens ÷ max_model_len`, so **halving `max_model_len` doubles concurrency** (16384→8192 gave 13×→26×). Raising util 0.90→0.95 barely moves the token count. Pick the smallest context your workload needs and you get more simultaneous requests for free. Throughput (req/s, tok/s) is compute-bound and essentially identical across both configs — shrinking context costs you nothing in throughput.

3. **`max_model_len` is part of vLLM's compile-cache key.** Changing it forces a full recompile on next start (our 8192 run cold-started at 565s vs 481s for the reused cache, purely from a 45s×N recompile). Changing `gpu_memory_utilization` alone does NOT miss the cache. **Pick your `max_model_len` once and hold it fixed across deploys** so the persisted torch.compile cache keeps being reused.

## Recommended deploy

`GPU=A100-80GB:1`, `TP=1`, `GPU_UTIL=0.95`, `MAX_LEN` = your real prompt ceiling, then frozen. This halves GPU cost vs 2×A100 for the same throughput class.

**Caveat on context length:** the real workload needs **16384**, not 8192 — production calls exceed 8K and reach 9K+, so 8192 truncates them. At bf16/16384 on one card the concurrency is only ~13× (below the ≥20-concurrent target). Getting 16K *and* ≥20× concurrency on a single card is the subject of follow-up quantization work (quantize the weights and/or the KV cache with negligible accuracy loss — ADR-0110). The 8192 table above is the measurement that proved `max_model_len` is the concurrency lever; treat it as evidence, not the deploy setting. Keep 2×A100 in reserve for genuinely long-context or higher-peak-concurrency needs.

## Not covered here (separate, in progress)

Cold start is still ~481s even with the compile cache reused — that's weight load (54GB) + CUDA-graph capture + engine init, **not** compilation. We're actively researching how to get restart under a minute (weight-streaming loaders, keep-warm, snapshot alternatives); that lands separately (ADR-0109). This handoff is only the single-GPU serving profile and the KV/concurrency levers.

Reference: ADR-0108, `scripts/modal_qwen3_27b_bench.py` (env-parametrized: `GPU`/`TP`/`MAX_LEN`/`GPU_UTIL`/`MAX_NUM_SEQS`/`N`/`MAX_TOK`/`CSWEEP`). Note if you copy the script: `modal run` does not inherit the local shell env, so runtime knobs must be baked into `vllm_image.env()` to reach the container.
