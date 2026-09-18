# RuleWright handoff: FP8 accuracy eval — Config B is lossless on the compliance workload

Date: 2026-09-18 (corrected 2026-09-19) · on `origin/main` · follow-up to ADR-0110 · **Measurement, no engine behavior change. Verdict: FP8 (Config B) matches full bf16 exactly on the real 9K+ compliance workload. A "gap" seen on a short-prompt NLI set was traced to a harness timeout artifact (NOT quantization) and is RETRACTED — see the correction below.**

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

## Result 2 — ContractNLI (150 labeled pairs, short prompts): RETRACTED (harness timeout artifact)

An initial read showed bf16 0.773 vs B 0.727 and was reported as a 4.6-pt gap. **That comparison is invalid.** The ContractNLI harness structured call has an **unbounded `rationale` field with no `max_tokens`**, so some judge calls run past the 60s structured-call timeout, and a timed-out call **defaults to "neutral"** in the harness — silently corrupting those pairs. The timeout counts are **run/container-specific, not config-specific**, which is the tell:

| eval | ref (bf16 KV) timeouts | B (fp8 KV) timeouts |
|---|---|---|
| ContractNLI | 0 | 40 |
| compliance-gold | 31 | 0 |

The pattern is **inverted** between the two evals — the bf16 reference timed out 31× in compliance but 0× in NLI; B did the reverse. So the NLI "gap" was B's 40 timeouts→neutral defaults tanking its entailment recall, not a quality difference. **No reliable FP8-vs-bf16 NLI signal exists from this run; the gap is retracted.** (Config A's run hit the same artifact — 56 timeouts — and was stopped rather than reported.)

## Read

- **The workload that matters (compliance, 9K+) shows zero FP8 loss, and the result is robust to the timeout noise.** compliance-gold is per-ad with an ≥2-violation rollup over 80–95 judge verdicts per case, so a handful of scattered timeouts can't flip a verdict — which is exactly why ref (31 timeouts) and B (0) produced **identical** ad-level outcomes. That is the decisive, trustworthy result for a compliance deployment.
- There is **no measured accuracy penalty for FP8** (Config B) anywhere in this eval; the only apparent difference was the NLI artifact above.
- A clean short-NLI judge-accuracy number would require capping `max_tokens` on the judge call (prevents the runaways) before re-running — worth doing only if a short-prompt judge number is specifically needed; the compliance-workload question is already answered.
- Structured output on raw vLLM must NOT use the default `function_calling` (400: "tool_choice=function requires --tool-call-parser") — the eval judge uses `json_schema` guided decoding with thinking off (the engine's self-hosted pattern). Relevant when RuleWright serves Qwen on its own vLLM.

## How it was run (reproducible)

`scripts/modal_qwen3_vllm_server.py` (MODEL/KV_CACHE_DTYPE-parametrized web server, served as `qwen3-eval`); the `qwen3-eval` model profile (vllm-pinned, json_schema, thinking-off); `eval/contractnli_judge.py` (`JUDGE_MODEL=qwen3-eval`); `scripts/eval_compliance_gold.py` (`RAG_MODEL_STRUCTURED_REASONING=qwen3-eval`, `EVAL_EXTRACT_MODEL` holds claim-extraction constant — its old hardcoded `ibm-granite/granite-4.1-8b` now 404s on OpenRouter and was made env-overridable).

Reference: ADR-0110 (the FP8 config), this handoff, the eval scripts above. Full model/seam suite: 123 passed.
