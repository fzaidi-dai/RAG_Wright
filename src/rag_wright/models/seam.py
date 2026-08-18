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

import asyncio
import logging
import os
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.runnables import Runnable, RunnableLambda
from langchain_openai import ChatOpenAI
from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError

from rag_wright.models.profiles import profile_for

log = logging.getLogger(__name__)


# Framework-native connection resilience (grounded: ChatOpenAI.max_retries/timeout + Runnable.with_retry).
# Retry is configured HERE at the single model construction point (ADR-0006), never hand-rolled at call sites.
#
# FREE-TEXT / plain build_model: the OpenAI SDK's own retry loop (max_retries) is the single layer, covering the
# native transients (429 / 5xx APIStatusError / connection / timeout), with the SDK's exponential backoff and
# Retry-After (429) handling intact. The okf_navigate agent's own model/sub-agent calls are covered separately
# by ModelRetryMiddleware.
#
# Bounded worst case (engine issue 0003 / ADR-0056): a persistent upstream stall makes each attempt hit the
# per-request timeout, then the SDK retries -- so the worst case is timeout x (max_retries + 1). At the old
# 120 x 7 that was ~14 min of idle-socket waiting (indistinguishable from a hang) on ANY free-text call
# (generation, reasoning, the ADR-0045 tag-parse structured path, vision-to-text, RLM chunk/synthesis). Bounded
# to 90 x 3 = 270s (~4.5 min): max_retries back to the SDK's own default of 2, timeout tightened but kept
# generous enough for legitimately longer free-text prose. (Unlike the structured path, the SDK loop stays, so
# its Retry-After handling is preserved -- there is no trade-off here.)
_MAX_RETRIES = 2  # SDK connection-resilience budget for 5xx/429/network on plain (free-text) calls (SDK default)
_TIMEOUT_S = 90.0  # per-request timeout for a plain call (a hang fails at 90s; worst case = 90 x (2+1) = 270s)
#
# STRUCTURED build_structured: ONE bounded retry layer, not two stacked (engine issue 0003 / ADR-0056). Before,
# build_structured wrapped `.with_retry` (3 attempts) AROUND a client that ALSO retried at the SDK (max_retries
# 6), so one logical structured call had a worst case of timeout x 6 x 3 = ~36 min -- long enough to look like a
# hang and to hold a synchronous single-doc ingest past any acceptable bound (NFR-1). Now the SDK loop is
# disabled for structured calls (max_retries=0) and the LangChain `.with_retry` is the SOLE layer, so the worst
# case is timeout x attempts = 60 x 3 = 180s, and the classifier's degrade path (empty sub-batch on exception)
# is reachable in ~3 min instead of ~36. TRADE-OFF: disabling the SDK loop loses its Retry-After (429) header
# handling; the tenacity exponential-jitter backoff on `.with_retry` substitutes for it (a documented, accepted
# substitution -- exponential-jitter backoff spaces out 429/5xx retries in its place).
_STRUCTURED_TIMEOUT_S = 60.0  # per-request timeout for a structured call (a hang fails here, not at 120s)
_STRUCTURED_RETRY_ATTEMPTS = 3  # the SOLE retry layer for structured calls; worst case ~= 60 x 3 = 180s
_STRUCTURED_RETRY_ON: tuple[type[BaseException], ...] = (
    # OpenRouter surfaces a 504 "operation was aborted" as a plain ValueError the status-code retry cannot
    # classify; the SDK transients are included here too because the SDK no longer retries them for structured
    # calls (this `.with_retry` is now the only layer that will).
    ValueError, APITimeoutError, APIConnectionError, RateLimitError, InternalServerError,
)


# TRUE wall-clock deadline for the ASYNC path (engine issue 0003 / ADR-0057). An httpx timeout is per-socket-op,
# so a slow-drip / SSE-keep-alive response resets the read clock indefinitely -- a single call ran 591s against a
# 60s timeout, and a 399s call succeeded with no exception at all. Only elapsed wall clock, enforced OUTSIDE the
# socket, can bound it. `asyncio.timeout` delivers a real CancelledError into the awaited call, so httpx closes
# the socket -- a true cancel, not the soft/leaked-thread cancel a synchronous watchdog gives.
_MODEL_DEADLINE_S = 180.0  # total wall-clock ceiling per LOGICAL model call (across bounded retries + backoff)


class ModelCallTimeout(Exception):
    """A logical model call exceeded the total wall-clock deadline (`_MODEL_DEADLINE_S`) and was truly cancelled
    (socket torn down). TERMINAL: a stalling peer is not a transient worth re-hitting, so this is deliberately
    NOT in `_STRUCTURED_RETRY_ON` and must be kept out of any pregel `retry_on` -- the caller degrades or
    dead-letters on it. It is the async fix a per-socket-op timeout cannot be (engine issue 0003 / ADR-0057)."""


def _backoff_s(attempt: int) -> float:
    """Exponential backoff with jitter (the Retry-After substitute), capped. `attempt` is 1-based."""
    return min(0.5 * (2 ** (attempt - 1)), 8.0) + random.uniform(0.0, 0.5)


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
    runnable is wrapped in a SINGLE bounded retry layer (`_with_bounded_retry`); the SDK's own retry loop is
    disabled here (max_retries=0) so the two do not stack into a ~36 min worst case (engine issue 0003 / ADR-0056).

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
    # ONE retry layer for structured calls (engine issue 0003 / ADR-0056): disable the SDK's own retry loop
    # (max_retries=0) and use a tighter per-request timeout, so `_with_bounded_retry` is the sole, bounded layer
    # (worst case = timeout x attempts, not multiplied by the SDK budget).
    overrides: dict[str, Any] = {"max_retries": 0, "timeout": _STRUCTURED_TIMEOUT_S}
    if max_tokens is not None:
        overrides["max_tokens"] = max_tokens
    inner = build_model(model_id, temperature=temperature, **overrides).with_structured_output(schema, **kwargs)
    # Dual-path during the async migration (ADR-0057): `.invoke` keeps the sync bounded retry (ADR-0056) for
    # not-yet-migrated callers; `.ainvoke` is the async bounded retry + TRUE wall-clock deadline. The sync path
    # is removed once all callers are async (Phase D). `RunnableLambda(func, afunc=...)` routes each accordingly.
    sync_runnable = _with_bounded_retry(inner, model_id)

    async def _adeadline(x: Any) -> Any:
        return await _ainvoke_bounded(inner, x, model_id)

    return RunnableLambda(sync_runnable.invoke, afunc=_adeadline)


async def _bounded_deadline(make_awaitable: Callable[[], Awaitable[Any]], model_id: str) -> Any:
    """Run an async model operation under the single bounded retry layer AND a true total wall-clock deadline
    (ADR-0057). `make_awaitable` is a factory returning a FRESH awaitable per attempt (a coroutine is single-use).
    Shared by the structured `.ainvoke` path and the free-text `astream` path.

    Bounded transient retries (the `_STRUCTURED_RETRY_ON` set) with per-attempt logging and exponential-jitter
    backoff, ALL under one `asyncio.timeout(_MODEL_DEADLINE_S)`. A slow-drip or connection-alive stall that a
    per-socket-op timeout never catches is CANCELLED at the deadline -- `asyncio.timeout` delivers CancelledError
    into the awaited call, httpx closes the socket -- and surfaces as a terminal `ModelCallTimeout`. Retry sleeps
    count against the same budget, so the total is bounded regardless of how it is spent."""
    async def _run() -> Any:
        for attempt in range(1, _STRUCTURED_RETRY_ATTEMPTS + 1):
            start = time.monotonic()
            try:
                return await make_awaitable()
            except _STRUCTURED_RETRY_ON as exc:
                log.warning("model call to %s failed after %.1fs (%s); retry %d/%d",
                            model_id, time.monotonic() - start, type(exc).__name__,
                            attempt, _STRUCTURED_RETRY_ATTEMPTS)
                if attempt >= _STRUCTURED_RETRY_ATTEMPTS:
                    raise
                await asyncio.sleep(_backoff_s(attempt))

    try:
        async with asyncio.timeout(_MODEL_DEADLINE_S):
            return await _run()
    except TimeoutError as exc:
        log.warning("model call to %s exceeded the %.0fs total wall-clock deadline; cancelled",
                    model_id, _MODEL_DEADLINE_S)
        raise ModelCallTimeout(f"model call to {model_id} exceeded {_MODEL_DEADLINE_S}s deadline") from exc


async def _ainvoke_bounded(runnable: Runnable, x: Any, model_id: str) -> Any:
    """The structured async path: `.ainvoke` under the shared bounded retry + total deadline (ADR-0057)."""
    return await _bounded_deadline(lambda: runnable.ainvoke(x), model_id)


# Idle-between-chunks guard for streamed free-text (ADR-0057, ASYNC-A3): if no chunk arrives within this window
# the stream raises -- a PRECISE drip-stall catch (the exact failure mode OpenRouter's SSE keep-alive comments
# hide) on top of the total deadline. A native `ChatOpenAI` field.
_STREAM_CHUNK_TIMEOUT_S = 60.0


async def astream_text(model_id: str, prompt: Any, *, temperature: float = 0.0,
                       max_tokens: int | None = None) -> str:
    """Free-text generation via streaming (ADR-0057, ASYNC-A3). Streams with `stream_chunk_timeout` for precise
    idle-drip detection, the total `asyncio.timeout` deadline for the whole call, and the bounded transient
    retries -- accumulating the streamed chunks into the full text (the same value
    `build_model(...).invoke(prompt).content` produced). `prompt` is a string or a message list."""
    overrides: dict[str, Any] = {
        "max_retries": 0, "timeout": _STRUCTURED_TIMEOUT_S, "stream_chunk_timeout": _STREAM_CHUNK_TIMEOUT_S}
    if max_tokens is not None:
        overrides["max_tokens"] = max_tokens
    client = build_model(model_id, temperature=temperature, **overrides)

    async def _consume() -> str:
        parts: list[str] = []
        async for chunk in client.astream(prompt):
            parts.append(str(chunk.content))
        return "".join(parts)

    return await _bounded_deadline(_consume, model_id)


def _with_bounded_retry(runnable: Runnable, model_id: str) -> Runnable:
    """Wrap a structured runnable in the SINGLE bounded retry layer (engine issue 0003 / ADR-0056): log each
    failed attempt (elapsed + exception type) so a retrying call is visibly working rather than a silent hang,
    then let `.with_retry` apply bounded, exponential-jitter backoff over the transient set. This is the ONLY
    retry layer for structured calls -- the SDK's own loop is disabled (max_retries=0 in `build_structured`) --
    so the worst-case wall clock is `_STRUCTURED_TIMEOUT_S x _STRUCTURED_RETRY_ATTEMPTS`, never multiplied by the
    SDK budget. The exponential-jitter backoff also substitutes for the SDK's lost Retry-After (429) handling."""

    def _attempt(x: Any) -> Any:
        start = time.monotonic()
        try:
            return runnable.invoke(x)
        except Exception as exc:  # noqa: BLE001 - log the transient, then re-raise for the bounded retry above
            log.warning(
                "structured call to %s failed after %.1fs (%s); retrying within the %d-attempt budget",
                model_id, time.monotonic() - start, type(exc).__name__, _STRUCTURED_RETRY_ATTEMPTS)
            raise

    return RunnableLambda(_attempt).with_retry(
        retry_if_exception_type=_STRUCTURED_RETRY_ON,
        wait_exponential_jitter=True,
        stop_after_attempt=_STRUCTURED_RETRY_ATTEMPTS,
    )
