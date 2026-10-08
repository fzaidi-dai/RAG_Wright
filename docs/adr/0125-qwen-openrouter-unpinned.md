# ADR-0125: Qwen3.8-27b on OpenRouter is unpinned (no provider routing)

**Status:** accepted · **Date:** 2026-10-09 · **Supersedes:** ADR-0111 · **Related:** ADR-0027 (provider flags live in
config + a dated ADR), ADR-0100 (profile-based routing), ADR-0110 (FP8 Qwen on Modal), ADR-0115 / ADR-0119
(classifiers and the Jev decision model)

## Context

ADR-0111 hard-pinned every OpenRouter string for Qwen3.8-27b (the engine-wide default model) to DeepInfra's bf16
endpoint: `{"provider": {"only": ["deepinfra/bf16"], "allow_fallbacks": false}}`, to guarantee full-precision,
consistent behaviour. On 2026-10-09 (found in PS-8a's live check) that endpoint was down (OpenRouter: status -5, 0%
uptime over 30 minutes) and requests to it hung with no first token until our deadline instead of failing. Every
Qwen call in the engine (seam and docling-graph extraction) blocked for its full 180 s deadline; party extraction
retried three times per document and then dead-lettered the document. Measured: the same party-extraction request
returned no token in 200 s with the pin, and the correct parties in 1.2 s without it.

The bf16 guarantee is no longer needed. Since ADR-0111 the pipeline's critical decisions moved off the LLM onto
trained classifiers and the Jev decision model (ADR-0115, ADR-0119), and FP8 Qwen3.8-27b was validated on Modal as
lossless against bf16 on the real workload (ADR-0110).

## Decision

The three OpenRouter Qwen3.8-27b profiles (`qwen/qwen3.8-27b`, `qwen3.8-27b-or`, `qwen3.8-27b-modal-or`) carry no
provider routing: OpenRouter routes them by its default, across any provider and quantization. The reasoning split
(on for structured calls, off for free text) is unchanged. On the docling-graph extraction surface, a model without
profile routing gets that path's existing default for unpinned models (the `OPENROUTER_SORT` preference, `latency` by
default), which is a sort preference, not a pin. The `OPENROUTER_PROVIDER_ORDER` / `OPENROUTER_PROVIDER` env
overrides remain for an explicit pin when a measurement needs one.

## Consequences

- One provider's outage no longer blocks the engine's default model.
- Outputs may come from different providers and quantizations from run to run; the engine's critical decisions do
  not depend on Qwen's numerics (classifiers, Jev), and FP8 was measured lossless (ADR-0110).
- The self-hosted path (ADR-0110) is unaffected: provider routing is OpenRouter-only.
