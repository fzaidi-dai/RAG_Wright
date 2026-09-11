# ADR-0100: a model string carries its own access — profile-based per-model routing

**Status:** accepted · **Date:** 2026-09-11 · **Prompted by:** RuleWright · **Generalizes:** ADR-0039 (self-hosted vLLM substrate, the `RAG_SERVING` switch), ADR-0027 (provider flags in config, not code) · **Related:** ADR-0097 (caller-configurable models)

## Context

A model was identified by a string (`qwen/qwen3.8-27b`, `ibm-granite/granite-4.2-8b`), and its **driving flags** lived in a per-model `ModelProfile`. But *where/how to reach it* lived in a **global** switch: `RAG_SERVING` (`openrouter` | `vllm`) picked one base_url/key for **every** model at once. Consequences: you could not **mix** backends (some stages on OpenRouter, some on a self-hosted vLLM/Modal server); the served-model id could not differ from our string; and construction was split across two surfaces (`build_model` used `_serving_config()`, extraction used `openrouter_model`/`vllm_model` branched on `RAG_SERVING`), which is why party/affiliation extraction hardcoded OpenRouter and silently ignored `RAG_SERVING`. The desired model: a model **string** fully describes both *which model* and *how to reach it*; the engine builds the client from that; callers only need the string vocabulary.

## Decision

Move access into the profile, keyed by string, and build every model from it.

- **`ModelProfile` gains access fields:** `backend` (`openrouter` | `vllm` | `ollama`; `None` = un-pinned), `served_model_id` (the id the backend expects — OpenRouter slug or vLLM `--served-model-name` — default = the string), and optional `base_url_env` / `api_key_env` (per-backend defaults otherwise). So two distinct vLLM/Modal deployments are just two strings.
- **One resolver, `resolve_connection(model_id)`** (seam): string → profile → `Connection(backend, provider, base_url, api_key, served_model_id)`. A profile that **pins** a backend routes there; an **un-pinned** string falls back to the global `RAG_SERVING` — so existing configs are unchanged (chosen: keep `RAG_SERVING` as the default, not remove it).
- **Both construction paths route through it:** `build_model` builds the client from the connection (not `_serving_config()`), and `default_extraction_model` builds its `ExtractionModel` from it. Graph (party/affiliation) extraction now goes through `default_extraction_model`, fixing the OpenRouter hardcode.
- **String scheme = explicit registry entries** (chosen): a backend-pinned string is a full `PROFILES` entry (e.g. `qwen3.8-27b-or` → OpenRouter slug `qwen/qwen3.8-27b`; `qwen3.8-27b-modal` → vLLM served-name `Qwen/Qwen3.8-27B`, reasoning via `chat_template_kwargs`). Unambiguous; a new model/route is one entry (+ ADR when its flags are empirical).

## Consequences

- **Mix backends per stage by choosing strings.** `qwen3.8-27b-or` and `qwen3.8-27b-modal` are the *same* model on two backends; a run can point extraction at the self-hosted server and the judge at OpenRouter (or any split) purely via strings. The product only knows the string vocabulary — no backend logic leaks into callers.
- **Back-compat preserved.** An un-pinned string (every existing slug) resolves via `RAG_SERVING` exactly as before; full suite 1540 passed with no change to un-pinned behavior. `RAG_MODEL_ALL` still works and now, combined with a pinned string, points every role at one *routed* model.
- **The two model surfaces are unified** under one resolver, and the per-model provider FLAGS still live in the profile (ADR-0027). The OpenRouter-only provider `extra_body` is simply absent from a vLLM-pinned profile (which uses `chat_template_kwargs` for the same reasoning control).
- **Per-backend flag correctness is the profile author's job:** reasoning is `{"reasoning":{"enabled":…}}` on OpenRouter but `{"chat_template_kwargs":{"enable_thinking":…}}` on vLLM; a pinned profile carries the right one for its backend.
- `openrouter_model` / `vllm_model` / `ollama_model` remain as explicit builders (back-compat), but the default extraction path no longer branches on `RAG_SERVING` — it resolves the profile.
