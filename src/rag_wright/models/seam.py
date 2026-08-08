"""The single construction point for the model client (T11).

Every model is built here, and `with_structured_output` is reached only here. A capability names a
model id (via `profiles.model_for(role)`) and asks the seam for a plain client or a
structured-output runnable; it never constructs `ChatOpenAI` or passes a provider/model flag itself.
The structured-only `extra_body` from the model's profile is applied to the forced structured call
alone, so free-text and reasoning calls on the same model are unaffected (CLAUDE.md standing rule).

Grounded against `langchain_openai.chat_models.base` (ADR-0001): `ChatOpenAI` takes `model`,
`temperature`, `api_key`, `base_url`; `with_structured_output(schema, method=..., **kwargs)` forwards
kwargs into the tool binding, so `extra_body` passed here binds to the structured runnable only.
"""

from __future__ import annotations

import os
from typing import Any

from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI

from rag_wright.models.profiles import profile_for


# Framework-native connection resilience (grounded: ChatOpenAI.max_retries/timeout + Runnable.with_retry;
# LangChain docs "Connection resilience" / "Fault tolerance"). Retry is configured HERE at the single model
# construction point (ADR-0006), never hand-rolled at call sites, so every caller inherits it uniformly.
# `max_retries` covers the OpenAI-native transient errors (429 / 5xx APIStatusError / connection / timeout);
# the `.with_retry` on the structured runnable additionally catches the transient errors OpenRouter surfaces as
# a plain ValueError (e.g. a 504 "operation was aborted"), which the status-code retry does not classify. The
# okf_navigate agent's own model/sub-agent calls are covered separately by ModelRetryMiddleware.
_MAX_RETRIES = 6  # matches the documented default connection-resilience budget for 5xx/429/network
_TIMEOUT_S = 120.0  # per-request timeout; OpenRouter can be slow on structured calls
_STRUCTURED_RETRY_ATTEMPTS = 3  # bounded retries for the OpenRouter-surfaced ValueError transient
_STRUCTURED_RETRY_ON: tuple[type[BaseException], ...] = (ValueError,)


def _openrouter_config() -> dict[str, Any]:
    """OpenRouter connection config from env (default/dev + fallback serving path; secrets only in `.env`)."""
    return {
        "api_key": os.environ["OPENROUTER_API_KEY"],
        "base_url": os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
    }


def serving_backend() -> str:
    """The selected serving backend name (`openrouter` | `vllm`) from `RAG_SERVING` (default `openrouter`).
    The single source of the serving switch -- the seam AND the docling-graph extraction path (a separate
    model surface, `dg_extraction.default_extraction_model`, MS1-3) both read it, so one env flips everything.
    """
    return os.getenv("RAG_SERVING", "openrouter").lower()


def _serving_config() -> dict[str, Any]:
    """OpenAI-compatible connection config for the SELECTED serving backend, chosen by env WITHOUT hardcoding
    a provider (MS1-1, ADR-0039). `openrouter` (default; dev + fallback) or `vllm` (the self-hosted Granite
    product substrate). vLLM needs `VLLM_BASE_URL` (an OpenAI-compatible base, e.g.
    `https://<app>.modal.run/v1`); `VLLM_API_KEY` is vLLM's `--api-key` bearer. The rest of the seam
    (per-model profile `structured_method`, `extra_body`, retries/timeout) is backend-agnostic and unchanged.
    """
    serving = serving_backend()
    if serving == "openrouter":
        return _openrouter_config()
    if serving == "vllm":
        return {
            "api_key": os.getenv("VLLM_API_KEY", "rw-vllm-dev-key"),
            "base_url": os.environ["VLLM_BASE_URL"].rstrip("/"),
        }
    raise ValueError(f"RAG_SERVING must be 'openrouter' or 'vllm', got {serving!r}")


def _provider_pin() -> dict[str, Any]:
    """OpenRouter provider routing from env (measurement/benchmark only). `OPENROUTER_PROVIDER` is a
    comma-separated provider list; `OPENROUTER_ALLOW_FALLBACKS` (true/false) toggles routing beyond that list.

    - single provider, no fallbacks (default): `OPENROUTER_PROVIDER=Cerebras` ->
      `{"provider": {"only": ["Cerebras"], "allow_fallbacks": False}}` (a hard pin, to measure one provider).
    - ordered preference + fallbacks: `OPENROUTER_PROVIDER=deepinfra/turbo,Cerebras,friendli` with
      `OPENROUTER_ALLOW_FALLBACKS=true` -> `{"provider": {"order": [...], "allow_fallbacks": True}}` (try those
      in order, then route around rate-limit/errors to any other provider).

    Empty when unset, so normal routing is unaffected."""
    raw = os.getenv("OPENROUTER_PROVIDER")
    if not raw:
        return {}
    providers = [p.strip() for p in raw.split(",") if p.strip()]
    if not providers:
        return {}
    allow = os.getenv("OPENROUTER_ALLOW_FALLBACKS", "").strip().lower() in ("1", "true", "yes")
    if len(providers) == 1 and not allow:
        return {"provider": {"only": providers, "allow_fallbacks": False}}  # hard single-provider pin
    return {"provider": {"order": providers, "allow_fallbacks": allow}}  # ordered preference, fallbacks per env


def build_model(model_id: str, *, temperature: float = 0.0, **overrides: Any) -> ChatOpenAI:
    """Construct the base client for `model_id`, carrying the profile's base `extra_body` (request-level
    provider routing, e.g. OpenRouter throughput sort -- a config-driven provider flag, ADR-0027).

    Model-level retry/timeout (framework connection resilience) are set here; a caller may override either.
    """
    params: dict[str, Any] = {"max_retries": _MAX_RETRIES, "timeout": _TIMEOUT_S}
    profile = profile_for(model_id)
    extra_body = {**(profile.extra_body or {}), **_provider_pin()}  # env pin merges over/into profile routing
    if extra_body:
        params["extra_body"] = extra_body
    params.update(overrides)  # caller overrides win
    return ChatOpenAI(
        model=model_id,
        temperature=temperature,
        **_serving_config(),  # OpenRouter (default) or vLLM-Granite, selected by RAG_SERVING (MS1-1)
        **params,
    )


def build_structured(
    model_id: str, schema: Any, *, include_raw: bool = False, temperature: float = 0.0,
    max_tokens: int | None = None,
) -> Runnable:
    """A structured-output runnable for `model_id`, driven by its profile.

    The profile supplies the method and the optional structured-only `extra_body`; the `extra_body`
    is bound to this forced structured call only. This is the sole path to `with_structured_output`. The
    runnable is wrapped with `.with_retry` so the OpenRouter-504-as-ValueError transient is retried (bounded).

    `temperature` defaults to 0 (deterministic-intent); a caller doing best-of-N self-consistency raises it
    to sample GENUINELY diverse structured completions (the base client's temperature, not a provider flag).
    `max_tokens` caps the completion length -- a safety net against a model that runs away to the context
    limit under a schema constraint (observed on self-hosted Gemma-4 with a mis-set chat template).
    """
    profile = profile_for(model_id)
    kwargs: dict[str, Any] = {"method": profile.structured_method, "include_raw": include_raw}
    # also put the env provider pin on the forced structured call (belt-and-suspenders: the base client carries
    # it too, but with_structured_output's extra_body should not drop it).
    structured_extra = {**(profile.structured_extra_body or {}), **_provider_pin()}
    if structured_extra:
        kwargs["extra_body"] = structured_extra
    overrides: dict[str, Any] = {"max_tokens": max_tokens} if max_tokens is not None else {}
    runnable = build_model(model_id, temperature=temperature, **overrides).with_structured_output(schema, **kwargs)
    return runnable.with_retry(
        retry_if_exception_type=_STRUCTURED_RETRY_ON,
        wait_exponential_jitter=True,
        stop_after_attempt=_STRUCTURED_RETRY_ATTEMPTS,
    )
