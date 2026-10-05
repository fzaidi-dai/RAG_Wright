# ADR-0110: FP8 KV cache lets Qwen3.8-27B serve 16K context at ≥20× concurrency on one A100-80GB

**Status:** accepted · **Date:** 2026-09-18 · **Resolves:** the concurrency shortfall flagged in ADR-0108 · **Related:** ADR-0108 (single-A100 serving profile; `max_model_len` is the concurrency lever), ADR-0109 (cold-start reduction), ADR-0039 (product substrate)

## Context

The real workload needs **`max_model_len=16384`** (production calls exceed 8K, reaching 9K+, so 8192 truncates them — ADR-0108). But at 16384 on one A100-80GB with **bf16 weights + bf16 KV**, the KV budget holds ~217K tokens → only **~13× concurrency**, below the product's **≥20 simultaneous requests** target. We were willing to quantize **if** accuracy loss is negligible. The question: which quantization of weights and/or KV cache reaches 16K at ≥20× with negligible loss, on Ampere (A100 has **no FP8 compute** — FP8 is a memory/bandwidth win only).

The KV-cache math (measured, util 0.95): concurrency = KV_tokens / max_model_len. To go from ~13× to ≥20× at fixed 16384 we need ~1.5× more KV tokens. Two independent levers do this: (1) **KV-cache FP8** (`--kv-cache-dtype fp8`) roughly halves bytes/token → ~2× tokens; (2) **weight quantization** frees VRAM (54 GB → ~27 GB FP8) for more KV. Either alone suffices; together they compound.

An architectural note specific to this model: `Qwen3.8-27B` is a hybrid GatedDeltaNet (linear-attention) + full-attention stack, so only the full-attention layers grow a per-token KV cache — which is why one 80GB card holds so many KV tokens, and why `--kv-cache-dtype fp8` (which quantizes that full-attention KV) is the effective lever.

## Decision

**Use `--kv-cache-dtype fp8` (E4M3) to reach 16K at ≥20× concurrency. FP8 KV alone — with weights left full bf16 (Config A) — already clears the target; official FP8 weights (Config B) are the higher-headroom option that also benefits the cold-start/snapshot work (ADR-0109).**

Measured on 1×A100-80GB, TP=1, max_model_len=16384, util 0.95, served/compiled path (`scripts/modal_qwen3_27b_bench.py`, `KV_CACHE_DTYPE=fp8`), clause→JSON workload, 48 req/level, **0 errors at every level**:

| | bf16-KV baseline¹ | **A: bf16 wt + FP8 KV** | **B: FP8 wt + FP8 KV** |
|---|---|---|---|
| Weights (VRAM) | bf16 ~54 GB | bf16 ~54 GB | FP8 ~27 GB |
| Available KV cache | ~17 GiB | 20.76 GiB | 42.9 GiB |
| GPU KV cache size | 217,239 tok | 417,310 tok | 863,533 tok |
| **Max concurrency @16K** | 13.26× | **25.47×** | **52.71×** |
| Warm cold-start | 481s | 439s | 481s |
| req/s @ C=64 | 5.36 | 5.21 | 6.43 |
| out-tok/s @ C=64 | 686 | 668 | 823 |
| TTFT p50 @ C=64 | 1.39s | 1.30s | 1.95s |
| total p50 @ C=64 | ~7.8s | 7.99s | 6.66s |

¹ the util-0.90/16384 point from ADR-0108; the ~1.9× KV-token jump A shows over it is the FP8-KV effect (plus a small util 0.90→0.95 bump). Engine logs confirm A = `quantization=None` + `kv_cache_dtype=fp8`; B = `quantization=fp8` + `kv_cache_dtype=fp8`, both on `sm80`.

Key findings:

- **FP8 KV alone gets us there.** Config A doubles KV tokens (217K→417K) → **25.47× at 16K**, clearing ≥20× while the dominant quality lever (the weights) stays bit-for-bit bf16. Only approximation is FP8 KV, which vLLM's 2026 study shows is **sub-point on the Qwen3.x-27B family and long-context-robust** (97–98% of baseline AUC to 128K) — negligible at 16K.
- **FP8 weights (B) add headroom AND throughput on this A100.** Contrary to the general Ampere FP8-Marlin caution, B measured **~23% higher throughput** and **lower total latency** than A, because smaller weights make the bandwidth-bound decode faster. The predicted Marlin **prefill tax shows only in TTFT** (~0.65s higher at C=64); for 256-token outputs decode dominates, so net latency improves. B also frees ~22 GB more VRAM → **52.71× concurrency**.
- **FP8 weights help the next phase.** ~27 GB resident weights roughly **halve the CPU-RAM that sleep-mode level-1 needs (~54→~27 GB)** and shrink the snapshot to restore (ADR-0109) — the concrete reason to prefer B once cold-start/snapshot cost is the binding constraint.

**Locked config (decided 2026-09-19): Config B — `Qwen/Qwen3.8-27B-FP8` + `--kv-cache-dtype fp8`.** Full serve line: `TP=1`, `--max-model-len 16384`, `--gpu-memory-utilization 0.95`, `--kv-cache-dtype fp8`, `--max-num-seqs` set to the real concurrency target, on `Qwen/Qwen3.8-27B-FP8`. The accuracy eval (below) found **no measured penalty for FP8**, so A and B are an accuracy tie on the workload that matters; B is chosen on the operational advantages (53× vs 25× concurrency, ~23% higher throughput, ~27 GB weights that halve the CPU-RAM + snapshot size for the ADR-0109 cold-start work). Config A (`Qwen/Qwen3.8-27B`, bf16 weights) remains the fallback only if a later, cleaner accuracy check surfaces an FP8-weight regression.

## Consequences

- The product serves its true 16K workload on a **single** A100-80GB at 25× (A) or 53× (B) concurrency — no 2×A100, no 8K truncation. This supersedes ADR-0108's interim 8192 deploy suggestion.
- **Accuracy validated on the real workload (2026-09-19), and B locked.** A judge-accuracy eval scored Config B (FP8 wt + FP8 KV) vs full bf16 (bf16 wt + bf16 KV) on the compliance judge: on **compliance-gold (FTC, 19 cases, the real 9K+ pipeline) B is IDENTICAL to bf16 case-for-case** (clearance-safety 1.00 both), and that result is robust (the ≥2-violation ad-level rollup over 80–95 verdicts/case absorbs scattered LLM-call timeouts). **No measured FP8 accuracy penalty anywhere.** (Harness: `scripts/modal_qwen3_vllm_server.py`, the `qwen3-eval` profile, `scripts/eval_compliance_gold.py`, `eval/contractnli_judge.py` — commit 85b8cb5; RuleWright handoff `docs/archive/handoffs/2026-09-18_fp8-accuracy-eval-configB_rulewright.md`.)
- **DEFERRED — capped ContractNLI recheck.** A short-prompt ContractNLI comparison in the same eval was corrupted by a harness artifact (its structured call has an unbounded `rationale` field with no `max_tokens` → some judge calls exceed the 60s timeout → the harness defaults them to "neutral"; timeout counts were run/container-specific, inverting between evals). The apparent NLI "gap" was retracted (commit 66f89c5), so it is NOT evidence against B. Still owed at some point: add a `max_tokens` cap to the judge call and re-run ContractNLI on A vs B for a clean short-prompt judge-accuracy number. Not blocking the B decision. Separately, if any future check shows a downward shift, calibrated KV scales (llm-compressor / `calculate_kv_scales=True`) are the lever (FP8 KV defaults to an uncalibrated scale of 1.0).
- FP8 KV is a storage format dequantized inside the attention kernel — throughput benefit, not latency (dequant not fused). Works on A100/Ampere (verified, `sm80`).
- **Cold start is unchanged (~440–480s)** — FP8 does not attack the CPU-bound engine-init/capture cost; that remains the ADR-0109 snapshot work. This model's startup includes a 51-graph piecewise CUDA-graph capture and (first-time only) ~106s kernel JIT; the persisted torch.compile cache covers only compilation, so capture is re-paid each cold start.

### Script changes (this ADR)
- `scripts/modal_qwen3_27b_bench.py`: added `KV_CACHE_DTYPE` env → `--kv-cache-dtype` (appended only when ≠ `auto`, so the bf16-KV compile-cache key is untouched); logs a `CONFIG:` line to distinguish A/B. Also set `HF_HUB_DISABLE_XET=1` — the HF Xet backend holds an open log handle under the cache dir that made `hf_vol.commit()` fail on a fresh download (`open files preventing the operation: xet/logs/...log`); disabling Xet uses the classic hf_transfer path and the Volume commit succeeds.
- Operational: run long Modal cold-start jobs with `modal run --detach` — a client-side gRPC "Deadline exceeded" cancelled a non-detached warm run mid-capture; detached keeps the app running server-side so the cache still persists.

### Sources (quantization research)
- FP8 KV cache (E4M3, ~2× tokens, long-context-robust): https://vllm-project.github.io/2026/04/22/fp8-kvcache.html , https://docs.vllm.ai/en/v0.9.2/features/quantization/quantized_kvcache.html
- Official FP8 weights "near-identical": https://huggingface.co/Qwen/Qwen3.8-27B-FP8 ; Ampere = FP8 Marlin weight-only (no compute win): https://docs.vllm.ai/en/v0.6.5/quantization/fp8.html
- Qwen3.x-27B robust to quantization: https://kaitchup.substack.com/p/qwen35-quantization-similar-accuracy
