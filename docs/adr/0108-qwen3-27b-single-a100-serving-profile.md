# ADR-0108: Qwen3.8-27B serves on a single A100-80GB; max_model_len is the concurrency lever

**Status:** accepted · **Date:** 2026-09-16 · **Related:** ADR-0039 (product substrate: self-hosted vLLM on Modal/A100), ADR-0100 (profile-based model routing), the `qwen3.8-27b-modal-or` profile (OpenRouter-pinned, Modal-destined)

## Context

Qwen3.8-27B (full bf16, ~54 GB weights) was benchmarked on Modal on **2× A100-80GB** (`tensor_parallel_size=2`). The question was whether it must run on two GPUs, or whether **one** A100-80GB suffices — and if so, what KV-cache budget and concurrency one GPU delivers. Halving the GPU footprint halves the serving cost for the same throughput class, which matters for the product's self-hosted substrate (ADR-0039).

The benchmark is the production-faithful served path (`vllm serve`, torch.compile ON, CUDA graphs, continuous batching under a concurrency sweep), not the offline `LLM()` engine — `scripts/modal_qwen3_27b_bench.py`.

## Decision

**Qwen3.8-27B serves comfortably on a single A100-80GB. Deploy it with `TP=1`, `gpu_memory_utilization=0.95`, and the smallest `max_model_len` the workload needs — `max_model_len` (not util) is the concurrency lever.**

Measured, 1× A100-80GB, served/compiled path, clause→JSON workload (256 output tokens/request), 48 requests per level:

| | **util 0.90, max_len 16384** | **util 0.95, max_len 8192** |
|---|---|---|
| Available KV cache | 17.13 GiB | 21.09 GiB |
| GPU KV cache size | 217,239 tokens | 212,028 tokens |
| **Max concurrency** | 13.26× | **25.88×** |
| Cold start (warm compile cache) | 481s | 565s (recompiled — see below) |
| req/s @ C=64 | 5.36 | 5.33 |
| out-tok/s @ C=64 | 686 | 683 |
| TTFT p50 @ C=64 | 1.39s | 1.27s |
| errors (all levels) | 0 | 0 |

Throughput/latency sweep (util 0.95 / max_len 8192): C=1→64 gives 0.22→5.33 req/s, 28→683 out-tok/s, TTFT p50 0.12→1.27s, total p50 4.37→7.80s, **0 errors at every level**.

Key facts that drive the decision:

- **A 2-GPU config does NOT "just work" on 1 GPU.** vLLM reads `--tensor-parallel-size` and hard-fails if it exceeds visible GPUs (`World size (2) is larger than the number of available GPUs (1)`). It does not auto-detect and adapt down. `TP` must be set to the GPU count — one required edit; everything else (KV sizing, concurrency) is then derived automatically.
- **Total KV *tokens* are ~constant (~212–217k) regardless of these settings** — that is the physical KV budget on one 80GB card after weights. Concurrency = `KV_tokens ÷ max_model_len`. So halving `max_model_len` (16384→8192) **doubles** concurrency (13×→26×), because each request reserves half the context. Raising util 0.90→0.95 buys ~4 GiB more KV but barely moves the token count. **`max_model_len` is the concurrency lever, not `gpu_memory_utilization`.** Pick the smallest context the product's prompts actually need.
- **Throughput is compute-bound, not KV-bound** at this request size: req/s and out-tok/s at C=64 are within noise between the two configs. Shrinking context costs no tokens/sec; it only buys headroom for more simultaneous long-context requests before queueing/preemption.
- **`max_model_len` is part of vLLM's torch.compile cache key.** Changing 16384→8192 was a cache miss and forced a recompile (`Compiling a graph for compile range (1, 2048) takes 45.64 s`), which is why the 8192 run cold-started slower (565s vs 481s) despite the persisted compile cache. Changing `gpu_memory_utilization` alone would NOT miss the cache. **Keep `max_model_len` fixed across deploys to reuse the warm compile cache; treat any `max_model_len` change as a one-time recompile cost.**

## Consequences

- The product can serve Qwen3.8-27B on **1× A100-80GB**, halving the GPU cost of the 2×A100 baseline for the same throughput class. The tradeoff is raw peak context length per request (one card's KV budget caps simultaneous long-context requests); at `max_model_len=8192` that is ~26× concurrency, which is ample for the clause/compliance workload.
- Single-GPU also removes the TP=2 NCCL init from the cold-start path (a secondary benefit, relevant to the cold-start work tracked separately — see ADR-0109).
- Recommended default deploy: `GPU=A100-80GB:1`, `TP=1`, `GPU_UTIL=0.95`, `MAX_LEN` set to the workload's real ceiling and then held fixed. **The real workload needs `MAX_LEN=16384`, not 8192** — production calls exceed 8K and reach 9K+ in practice, so 8192 would truncate them. At 16384 on one bf16 card the concurrency is only ~13× (below the product's ≥20-concurrent target), which is what motivates the quantization work in **ADR-0110** (quantize weights and/or the KV cache to fit 16K at ≥20× concurrency with negligible accuracy loss). The 8192 numbers above stay recorded as the measurement that established `max_model_len` as the concurrency lever, not as the deploy setting. 2×A100 remains available for genuinely long-context or higher-peak-concurrency needs (not benchmarked head-to-head here; deferred until a workload requires it).
- Cold start remains ~481s even with the compile cache reused — that is the weight-load + CUDA-graph-capture + engine-init cost, and is the subject of separate cold-start-reduction work (ADR-0109 / the warm-up research). This ADR only establishes the single-GPU serving profile and the KV/concurrency levers.

Bench script: `scripts/modal_qwen3_27b_bench.py` (env-parametrized: `GPU`, `TP`, `MAX_LEN`, `GPU_UTIL`, `MAX_NUM_SEQS`, `N`, `MAX_TOK`, `CSWEEP`). Note: `modal run` does not inherit the local shell env, so runtime knobs are baked into `vllm_image.env()` to reach the container.
