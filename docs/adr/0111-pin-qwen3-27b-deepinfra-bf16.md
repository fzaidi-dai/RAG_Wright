# ADR-0111: pin Qwen3.8-27b to DeepInfra's bf16 endpoint on OpenRouter (engine-wide)

**Status:** accepted · **Date:** 2026-09-20 · **Related:** ADR-0027 (provider flags live in config + a dated ADR, never in node code), ADR-0100 (profile-based routing), ADR-0110 (FP8 config on the self-hosted path)

## Context

Qwen3.8-27b is the engine-wide default (`_PRODUCT_LLM` / `_PRODUCT_EXTRACT_DEFAULT`), served via OpenRouter for now (the self-hosted Modal path awaits the ADR-0109 cold-start work). OpenRouter routes a model across multiple upstream providers and **quantization variants** (bf16, fp8, int) unless constrained. Its prior routing on our Qwen profiles was `{"provider": {"sort": "throughput"}}`, which optimizes for speed and can land on a **quantized** provider endpoint — so the model's numerical behavior could silently vary run-to-run with whichever provider/quantization OpenRouter picked. We want deterministic, full-precision behavior: always DeepInfra's **bf16** endpoint.

## Decision

**Hard-pin every OpenRouter string for Qwen3.8-27b to `deepinfra/bf16`, with fallbacks disabled, sourced from the model profile so the pin holds on BOTH the seam and the extraction surface.**

- Profiles (`models/profiles.py`): the three OpenRouter Qwen3.8-27b strings — `qwen/qwen3.8-27b`, `qwen3.8-27b-or`, `qwen3.8-27b-modal-or` (the product default) — now carry
  `extra_body={"provider": {"only": ["deepinfra/bf16"], "allow_fallbacks": False}}`.
  `only` + `allow_fallbacks: False` is a **hard pin**: OpenRouter can never route to another provider or a quantized variant. The reasoning split (`structured_extra_body` on / `text_extra_body` off) is unchanged. The self-hosted `qwen3.8-27b-modal` (vLLM backend) is untouched — provider routing is OpenRouter-only.
- Extraction surface (`capabilities/dg_extraction.py`): the docling-graph litellm path built its own OpenRouter provider routing from env (`OPENROUTER_SORT`/`OPENROUTER_PROVIDER_ORDER`), independent of the profile — so a profile pin would NOT have reached clause/claim/party extraction (the largest Qwen usage). It now **honors the model profile's provider routing** (ADR-0100): `ExtractionModel` gained a `provider_routing` field, `default_extraction_model` sources it from `profile_for(model).extra_body["provider"]`, and `_call_api` applies it with precedence **`OPENROUTER_PROVIDER_ORDER` env (measurement override) > profile pin > `OPENROUTER_SORT` sort default**. Un-pinned models (no `provider` in their profile) keep the sort default; granite-4.2-8b's profile already carries `{"sort":"latency"}` (== the old default), so non-Qwen extraction is unchanged.

## Consequences

- Every Qwen3.8-27b call — seam (judge/generation/structured) and extraction (clause/claim/party/requirement) — routes to **DeepInfra bf16** on OpenRouter, verified live (a raw call with the hard pin returned `provider = DeepInfra`; the hard pin returning 200 also proves the slug is valid, since `allow_fallbacks: False` errors on an invalid/unavailable provider).
- Full-precision, deterministic-provider behavior: no silent drift onto a quantized provider variant. This complements the self-hosted FP8 decision (ADR-0110): the OpenRouter dev/fallback path is now explicitly **bf16**, so it is a faithful full-precision reference for the self-hosted path, not a moving target.
- Availability trade-off: with fallbacks off, if DeepInfra's bf16 endpoint is down/throttled, Qwen calls fail rather than route elsewhere. That is intentional (the point is guaranteed bf16); the retry/timeout layers still apply, and the `OPENROUTER_PROVIDER_ORDER` / `OPENROUTER_PROVIDER` env overrides remain for measurement or an emergency reroute without a code change.
- Provider routing stays in config + this dated ADR (ADR-0027); no provider flag entered node/agent code. The extraction surface is now aligned with ADR-0100 (profile is the single source of routing truth for both surfaces).

Tests: `tests/models/test_profile_seam.py` (all three Qwen strings pin deepinfra/bf16), `tests/models/test_serving_wiring.py` (extraction carries the profile pin; un-pinned carries none). Full model + dg-extraction suite: 142 passed. Live: raw OpenRouter call routed to DeepInfra.
