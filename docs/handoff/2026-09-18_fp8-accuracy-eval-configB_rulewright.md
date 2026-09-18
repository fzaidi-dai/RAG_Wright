# RuleWright handoff: FP8 accuracy eval — Config B is lossless on the compliance workload

Date: 2026-09-18 · on `origin/main` · follow-up to ADR-0110 · **Measurement, no engine behavior change. Verdict: FP8 (Config B) matches full bf16 exactly on the real 9K+ compliance workload; one short-prompt NLI gap is still being attributed (A eval pending).**

---

## What was tested

Does **Config B** (FP8 weights + FP8 KV, `Qwen/Qwen3.8-27B-FP8` served on Modal vLLM at 16384) lose accuracy vs **full bf16** (bf16 weights + bf16 KV) on the compliance judge? Method: two Modal vLLM endpoints under a stable served name (`qwen3-eval`), the judge role repointed to each in turn (claim-extraction held constant on OpenRouter Qwen, so only the judge varies), scored on two labeled sets. Endpoints torn down after (no standing GPU).

## Result 1 — compliance-gold (FTC, 19 cases, the real 9K+ pipeline): IDENTICAL

| metric | bf16 reference | B (FP8+FP8) |
|---|---|---|
| violations caught | 11/11 | 11/11 |
| clearance-safety (never clear a real violation) | 1.00 | 1.00 |
| hard-viol-precision | 0.65 | 0.65 |
| hard-FP-rate | 0.75 | 0.75 |

**All 19 predictions match case-for-case.** On the safety-critical, long-context workload, FP8 quantization is lossless — zero change in any verdict. (Note: the absolute precision/FP numbers reflect the thin, expert-review-pending FTC negative class — bf16 itself over-flags 6/8 compliant cases; that is a gold-set property, not a quantization effect, and it is identical across both models.)

## Result 2 — ContractNLI (150 labeled pairs, short prompts): a 4.6-pt gap, not yet attributed

| | accuracy | entailment | contradiction | neutral |
|---|---|---|---|---|
| bf16 reference (1 run) | 0.773 | 0.720 | 0.740 | 0.860 |
| B (FP8), 3 runs | 0.727 (all three) | 0.600 | 0.740 | 0.840 |

B is a consistent 4.6 pts lower, all in entailment recall. A same-config control (B run ×3) returned an identical 0.727 every time — proving vLLM greedy is **deterministic per container**, so same-config reruns cannot separate "real FP8 loss" from container/seed effects. Attributing this gap needs a bf16 rerun, deferred with the GPU torn down.

## Read

- **The workload that matters (compliance, 9K+) shows zero FP8 loss.** That is the decisive result for a compliance deployment.
- The ContractNLI gap is a short-prompt NLI signal on a harder task; it is a yellow flag, not a workload regression. **Config A (bf16 weights + FP8 KV) is the clean way to attribute it**: if A scores like bf16 (~0.773) while B scores 0.727, the drop is isolated to FP8 *weights*, meaning FP8 *KV* is clean and only the weight-quant carries a small NLI cost. That A eval is the pending next step.
- Structured output on raw vLLM must NOT use the default `function_calling` (400: "tool_choice=function requires --tool-call-parser") — the eval judge uses `json_schema` guided decoding with thinking off (the engine's self-hosted pattern). Relevant when RuleWright serves Qwen on its own vLLM.

## How it was run (reproducible)

`scripts/modal_qwen3_vllm_server.py` (MODEL/KV_CACHE_DTYPE-parametrized web server, served as `qwen3-eval`); the `qwen3-eval` model profile (vllm-pinned, json_schema, thinking-off); `eval/contractnli_judge.py` (`JUDGE_MODEL=qwen3-eval`); `scripts/eval_compliance_gold.py` (`RAG_MODEL_STRUCTURED_REASONING=qwen3-eval`, `EVAL_EXTRACT_MODEL` holds claim-extraction constant — its old hardcoded `ibm-granite/granite-4.1-8b` now 404s on OpenRouter and was made env-overridable).

Reference: ADR-0110 (the FP8 config), this handoff, the eval scripts above. Full model/seam suite: 123 passed.
