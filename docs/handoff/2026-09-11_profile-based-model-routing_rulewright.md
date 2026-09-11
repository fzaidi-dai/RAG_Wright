# RuleWright handoff: a model string now carries its own access (profile-based routing)

Date: 2026-09-11 · on `origin/main` (commit `4221231`) · ADR-0100 · **No caller API change. Un-pinned strings behave exactly as before.**

---

## What you asked for, and what landed

A model **string** now fully describes both *which model* and *how to reach it*. The engine resolves the string → its profile → builds the client with the right backend, base_url, key, and served-model id. **You only need to know the string vocabulary**, and you can **mix** OpenRouter and self-hosted vLLM/Modal per stage by choosing different strings.

- `ModelProfile` gained access fields: `backend` (`openrouter`|`vllm`|`ollama`; omit = un-pinned), `served_model_id` (the id the backend actually expects), `base_url_env`/`api_key_env` (per-backend defaults otherwise).
- `resolve_connection(model_id)` is the single resolver; `build_model` and `default_extraction_model` both build from it. Party/affiliation extraction now routes through it too (it was hardcoded to OpenRouter — fixed).

## The two mechanics you use

**1. Pinned strings — pick the backend by name.** We registered two example entries for the same Qwen model:

| String | Routes to | Served id | Notes |
|---|---|---|---|
| `qwen3.8-27b-or` | OpenRouter | `qwen/qwen3.8-27b` | its provider-routing + reasoning-field flags |
| `qwen3.8-27b-modal` | self-hosted vLLM (`VLLM_BASE_URL`) | `Qwen/Qwen3.8-27B` | reasoning via vLLM `chat_template_kwargs`; no OpenRouter provider flag |

So `RAG_MODEL_ALL=qwen3.8-27b-modal` points **everything** at your Modal server; `extract_model="qwen3.8-27b-or"` on one stage sends just that stage to OpenRouter. Mixing is just strings.

**2. Un-pinned strings — unchanged.** Any existing slug (`qwen/qwen3.8-27b`, `ibm-granite/granite-4.2-8b`) has no pinned backend, so it still falls back to the global `RAG_SERVING` (`openrouter`|`vllm`) exactly as today. Your current configs keep working with zero changes.

## How this simplifies your "Qwen everywhere on Modal" setup

Instead of `RAG_SERVING=vllm` + `RAG_MODEL_ALL=qwen/qwen3.8-27b` + making sure `VLLM_BASE_URL` and the served-name line up, you can now just:

```
RAG_MODEL_ALL=qwen3.8-27b-modal
VLLM_BASE_URL=https://<your-qwen-app>.modal.run/v1
VLLM_API_KEY=<your key>
```

The `qwen3.8-27b-modal` profile already carries `backend=vllm` + `served_model_id=Qwen/Qwen3.8-27B` + the vLLM reasoning flags, so every stage (extraction included) routes to your server with the correct served-name. No `RAG_SERVING`, no per-stage `extract_model` needed.

## Adding a model / a second server

- **A new model or backend variant** = one `PROFILES` entry (string → `ModelProfile(backend=…, served_model_id=…, flags…)`). Tell us the model + backend and we'll add it (with an ADR if its provider flags are empirical).
- **A second Modal deployment** = a string with `base_url_env="VLLM_BASE_URL_2"` (or any env name) — so two servers are just two strings.

## One correctness note (profile authors, i.e. us)
Per-backend flags differ: reasoning is `{"reasoning":{"enabled":…}}` on OpenRouter but `{"chat_template_kwargs":{"enable_thinking":…}}` on vLLM. Each pinned profile carries the right one. If you standardize on a Qwen id/served-name different from `Qwen/Qwen3.8-27B`, tell us and we'll set `served_model_id` to match your server.

Reference: ADR-0100, `models/profiles.py` (`ModelProfile` access fields, the `qwen3.8-27b-or`/`-modal` entries), `models/seam.py::resolve_connection` + `build_model`, `capabilities/dg_extraction.py::default_extraction_model`, `capabilities/graph_extraction.py`.
