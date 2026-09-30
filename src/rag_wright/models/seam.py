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
from dataclasses import dataclass
from typing import Any, Optional

from langchain_core.runnables import Runnable, RunnableLambda
from langchain_openai import ChatOpenAI
from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError
from pydantic import PrivateAttr

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
# per-request timeout for a structured call. Default 60s (a hang fails here, not at 120s). Env-overridable because
# reasoning-ON structured calls under batch load can legitimately run ~50-70s (the server returns 200 OK, but the
# client would give up at 60s and retry -> a retry pileup); a bulk job can raise this (e.g. 150) to let them finish.
_STRUCTURED_TIMEOUT_S = float(os.getenv("RAG_STRUCTURED_TIMEOUT_S", "60"))
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


@dataclass(frozen=True)
class Connection:
    """ADR-0100: how to reach a model -- resolved from its profile. `backend` is the routing target; `provider`
    is the litellm provider name (for the extraction path); `served_model_id` is the id the backend expects."""

    backend: str          # openrouter | vllm | ollama
    provider: str         # litellm provider: openrouter | hosted_vllm | ollama
    base_url: str
    api_key: Optional[str]
    served_model_id: str


def resolve_connection(model_id: str) -> Connection:
    """ADR-0100: resolve a model STRING to its access (backend + base_url + key + the id the backend expects),
    from its profile. A profile that PINS a `backend` routes there (so different strings can target OpenRouter
    vs a self-hosted vLLM/Modal server -- mix at will); an un-pinned profile falls back to the global
    `RAG_SERVING` default (back-compat). `base_url_env`/`api_key_env` on the profile override the per-backend
    default env vars, so two distinct vLLM/Modal deployments are just two strings."""
    profile = profile_for(model_id)
    backend = profile.backend or serving_backend()  # un-pinned -> RAG_SERVING
    served = profile.served_model_id or model_id
    if backend == "openrouter":
        return Connection(
            backend, "openrouter",
            os.getenv(profile.base_url_env or "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            os.environ.get(profile.api_key_env or "OPENROUTER_API_KEY"), served)
    if backend == "vllm":
        return Connection(
            backend, "hosted_vllm",
            os.environ[profile.base_url_env or "VLLM_BASE_URL"].rstrip("/"),
            os.getenv(profile.api_key_env or "VLLM_API_KEY", "rw-vllm-dev-key"), served)
    if backend == "ollama":
        return Connection(
            backend, "ollama",
            os.getenv(profile.base_url_env or "OLLAMA_BASE_URL", "http://localhost:11434"), None, served)
    raise ValueError(f"unknown backend {backend!r} for model {model_id!r} (openrouter | vllm | ollama)")


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


class _CostCapturingChatOpenAI(ChatOpenAI):
    """ISSUE-0021: OpenRouter returns the ACTUAL per-call `cost` on the final streaming chunk's `usage`, but
    LangChain's streaming normalization (`_create_usage_metadata`) whitelists token counts and DROPS `cost` --
    unlike `ainvoke`, which preserves the raw `token_usage` in `response_metadata`. Tap the raw chunk in the
    (overridable) per-chunk converter to capture the real cost -- a pass-through, exactly like the litellm path --
    WITHOUT touching any of the streaming / idle-drip / deadline / retry machinery. `_cost_holder` is per-instance,
    so each `build_model(...)` call gets a fresh capture."""

    _cost_holder: dict[str, Any] = PrivateAttr(default_factory=dict)

    def _convert_chunk_to_generation_chunk(self, chunk: dict, default_chunk_class: type,
                                           base_generation_info: dict | None) -> Any:
        usage = chunk.get("usage") or {}
        if usage.get("cost") is not None:
            self._cost_holder["cost"] = usage.get("cost")
        return super()._convert_chunk_to_generation_chunk(chunk, default_chunk_class, base_generation_info)


def build_model(model_id: str, *, temperature: float = 0.0, _client_cls: type[ChatOpenAI] | None = None,
                **overrides: Any) -> ChatOpenAI:
    """Construct the base client for `model_id`, carrying the profile's base `extra_body` (request-level
    provider routing, e.g. OpenRouter throughput sort -- a config-driven provider flag, ADR-0027).

    Model-level retry/timeout (framework connection resilience) are set here; a caller may override either.
    `_client_cls` lets a caller substitute a thin ChatOpenAI subclass (e.g. astream_text's cost-capturing client,
    issue 0021); it defaults to the plain client so every other caller is unchanged.
    """
    params: dict[str, Any] = {"max_retries": _MAX_RETRIES, "timeout": _TIMEOUT_S}
    profile = profile_for(model_id)
    caller_extra = overrides.pop("extra_body", None)  # a per-call extra_body (e.g. astream_text's text_extra_body)
    # MERGE order: profile base routing < env provider pin < caller extra_body -- so a caller adds/overrides a key
    # (e.g. reasoning) WITHOUT dropping the profile's provider routing (a plain params.update would clobber it all).
    extra_body = {**(profile.extra_body or {}), **_provider_pin(), **(caller_extra or {})}
    if extra_body:
        params["extra_body"] = extra_body
    params.update(overrides)  # caller overrides win
    cls = _client_cls or ChatOpenAI  # resolve at call time so a monkeypatched `seam.ChatOpenAI` (tests) is honored
    conn = resolve_connection(model_id)  # ADR-0100: backend + base_url + key + served id, from the profile
    return cls(
        model=conn.served_model_id,  # the id the backend expects (== model_id for an un-pinned string)
        temperature=temperature,
        base_url=conn.base_url,
        api_key=conn.api_key,
        **params,
    )


def build_structured(
    model_id: str, schema: Any, *, include_raw: bool = False, temperature: float = 0.0,
    max_tokens: int | None = None, label: str | None = None,
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
    from rag_wright.models import tracing
    from rag_wright.models import usage as usage_acct

    profile = profile_for(model_id)
    # ISSUE-0025 / issue 0042: the forced-structured path discards the raw response, so tokens + OpenRouter's
    # ACTUAL cost (which `ainvoke` surfaces on the raw, issue 0021) would be lost. The runnable is built ONCE and
    # invoked (concurrently) later, possibly inside a `usage_scope()` entered after build -- so we cannot decide
    # per-call at build time; we ALWAYS force `include_raw=True` internally and read usage off the raw at finish,
    # recording it into any active usage scope (issue 0042) and emitting a Langfuse generation when traced
    # (ISSUE-0025). The caller's exact output shape and the raise-on-parse-failure contract are RESTORED below, so
    # this is purely a client-side capture (no extra tokens, no extra round trip) with an unchanged external shape.
    traced = tracing.tracing_on()
    effective_include_raw = True
    kwargs: dict[str, Any] = {"method": profile.structured_method, "include_raw": effective_include_raw}
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
    inner: Runnable = build_model(model_id, temperature=temperature, **overrides).with_structured_output(
        schema, **kwargs)
    if not include_raw:
        # we forced include_raw for instrumentation, but the caller wanted the parsed value with the native
        # raise-on-parse-failure contract -- restore it so the bounded retry sees the SAME error it would have.
        inner = inner | RunnableLambda(_raise_on_parse_error)
    # Dual-path during the async migration (ADR-0057): `.invoke` keeps the sync bounded retry (ADR-0056) for
    # not-yet-migrated callers; `.ainvoke` is the async bounded retry + TRUE wall-clock deadline. The sync path
    # is removed once all callers are async (Phase D). `RunnableLambda(func, afunc=...)` routes each accordingly.
    sync_runnable = _with_bounded_retry(inner, model_id)

    def _finish(result: Any, latency_ms: float, gen: Any) -> Any:
        raw = result.get("raw") if isinstance(result, dict) else None
        inp, out, cost = _usage_from_raw(raw)
        # issue 0042: record into any active usage scope (no-op if none) -- always, regardless of tracing.
        usage_acct.record_usage(model_id, input_tokens=inp, output_tokens=out, cost=cost, latency_ms=latency_ms)
        if gen is not None:  # ISSUE-0025 / 0048: end the generation opened before the call (real span duration)
            parsed = result.get("parsed") if isinstance(result, dict) else None
            gid = _provider_gen_id(raw)  # 0048: OpenRouter generation id for queue-vs-gen attribution
            tracing.finish_generation(
                gen, output=parsed, usage=({"input": inp, "output": out} if (inp or out) else None),
                cost=cost, latency_ms=latency_ms,
                metadata=({"openrouter_generation_id": gid} if gid else None))
        # hand the caller back the exact shape it asked for (we forced include_raw internally)
        return result if include_raw else (result.get("parsed") if isinstance(result, dict) else result)

    def _sync(x: Any) -> Any:
        # 0048: open the generation BEFORE the call so Langfuse's own latency is the real duration.
        gen = tracing.start_generation(model=model_id, input=x, label=label, stage="build_structured") \
            if traced else None
        t0 = time.monotonic()
        return _finish(sync_runnable.invoke(x), (time.monotonic() - t0) * 1000.0, gen)

    async def _adeadline(x: Any) -> Any:
        gen = tracing.start_generation(model=model_id, input=x, label=label, stage="build_structured") \
            if traced else None
        t0 = time.monotonic()
        result = await _ainvoke_bounded(inner, x, model_id, label)
        return _finish(result, (time.monotonic() - t0) * 1000.0, gen)

    return RunnableLambda(_sync, afunc=_adeadline)


def _raise_on_parse_error(result: Any) -> Any:
    """When `include_raw=True` was forced for instrumentation but the caller wanted the parsed value, restore the
    native `include_raw=False` contract: re-raise the exact parse error so the bounded retry retries identically."""
    if isinstance(result, dict) and result.get("parsing_error") is not None:
        raise result["parsing_error"]
    return result


def _usage_from_raw(raw: Any) -> tuple[int, int, Any]:
    """(input_tokens, output_tokens, cost) off a raw structured response (`include_raw=True`); `(0, 0, None)`
    when absent. `cost` is OpenRouter's ACTUAL per-call cost (issue 0021); `None` = the backend surfaced none
    (priced from a table by Langfuse; counted as `calls_without_cost` in the usage scope, never as $0)."""
    if raw is None:
        return 0, 0, None
    um = getattr(raw, "usage_metadata", None) or {}
    token_usage = (getattr(raw, "response_metadata", {}) or {}).get("token_usage") or {}
    return int(um.get("input_tokens", 0) or 0), int(um.get("output_tokens", 0) or 0), token_usage.get("cost")


def _provider_gen_id(raw: Any) -> Optional[str]:
    """The provider's generation id off a raw response (LangChain sets it on `.id`; else `response_metadata.id`).
    On OpenRouter this resolves at `/api/v1/generation?id=` -- recording it lets a slow call be ATTRIBUTED (queue
    vs generation time) rather than guessed (issue 0048). None on backends that don't surface one (e.g. vLLM)."""
    if raw is None:
        return None
    return getattr(raw, "id", None) or (getattr(raw, "response_metadata", {}) or {}).get("id")


def _call_desc(model_id: str, label: str | None) -> str:
    """The model-call description used in the deadline/retry warnings + the timeout message. ADR-0058 side-fix
    (issue 0004): include the STAGE/call-site (`label`) when the caller supplies it, so a timeout names WHICH
    stage was cancelled (e.g. `granite-4.2-8b for semantic_chunking.discover`), not just the model."""
    return f"{model_id} for {label}" if label else model_id


async def _bounded_deadline(
    make_awaitable: Callable[[], Awaitable[Any]], model_id: str, label: str | None = None
) -> Any:
    """Run an async model operation under the single bounded retry layer AND a true total wall-clock deadline
    (ADR-0057). `make_awaitable` is a factory returning a FRESH awaitable per attempt (a coroutine is single-use).
    Shared by the structured `.ainvoke` path and the free-text `astream` path. `label` names the call-site/stage
    in the warnings (ADR-0058, issue 0004 side-fix).

    Bounded transient retries (the `_STRUCTURED_RETRY_ON` set) with per-attempt logging and exponential-jitter
    backoff, ALL under one `asyncio.timeout(_MODEL_DEADLINE_S)`. A slow-drip or connection-alive stall that a
    per-socket-op timeout never catches is CANCELLED at the deadline -- `asyncio.timeout` delivers CancelledError
    into the awaited call, httpx closes the socket -- and surfaces as a terminal `ModelCallTimeout`. Retry sleeps
    count against the same budget, so the total is bounded regardless of how it is spent."""
    desc = _call_desc(model_id, label)

    async def _run() -> Any:
        for attempt in range(1, _STRUCTURED_RETRY_ATTEMPTS + 1):
            start = time.monotonic()
            try:
                return await make_awaitable()
            except _STRUCTURED_RETRY_ON as exc:
                log.warning("model call to %s failed after %.1fs (%s); retry %d/%d",
                            desc, time.monotonic() - start, type(exc).__name__,
                            attempt, _STRUCTURED_RETRY_ATTEMPTS)
                if attempt >= _STRUCTURED_RETRY_ATTEMPTS:
                    raise
                await asyncio.sleep(_backoff_s(attempt))

    try:
        async with asyncio.timeout(_MODEL_DEADLINE_S):
            return await _run()
    except TimeoutError as exc:
        log.warning("model call to %s exceeded the %.0fs total wall-clock deadline; cancelled",
                    desc, _MODEL_DEADLINE_S)
        raise ModelCallTimeout(f"model call to {desc} exceeded {_MODEL_DEADLINE_S}s deadline") from exc


async def _ainvoke_bounded(runnable: Runnable, x: Any, model_id: str, label: str | None = None) -> Any:
    """The structured async path: `.ainvoke` under the shared bounded retry + total deadline (ADR-0057)."""
    return await _bounded_deadline(lambda: runnable.ainvoke(x), model_id, label)


# Idle-between-chunks guard for streamed free-text (ADR-0057, ASYNC-A3): if no chunk arrives within this window
# the stream raises -- a PRECISE drip-stall catch (the exact failure mode OpenRouter's SSE keep-alive comments
# hide) on top of the total deadline. A native `ChatOpenAI` field.
_STREAM_CHUNK_TIMEOUT_S = 60.0


async def astream_text(model_id: str, prompt: Any, *, temperature: float = 0.0,
                       max_tokens: int | None = None, label: str | None = None) -> str:
    """Free-text generation via streaming (ADR-0057, ASYNC-A3). Streams with `stream_chunk_timeout` for precise
    idle-drip detection, the total `asyncio.timeout` deadline for the whole call, and the bounded transient
    retries -- accumulating the streamed chunks into the full text (the same value
    `build_model(...).invoke(prompt).content` produced). `prompt` is a string or a message list."""
    import time as _time

    from rag_wright.models import tracing
    from rag_wright.models import usage as usage_acct

    overrides: dict[str, Any] = {
        "max_retries": 0, "timeout": _STRUCTURED_TIMEOUT_S, "stream_chunk_timeout": _STREAM_CHUNK_TIMEOUT_S}
    if max_tokens is not None:
        overrides["max_tokens"] = max_tokens
    # The FREE-TEXT reasoning control (profile.text_extra_body): applied to this streaming path only, never to a
    # forced structured call. For a reasoning model this must be EXPLICIT -- unset, qwen3.8 streaming intermittently
    # returns empty content (issue 0020). build_model merges it OVER the profile's base extra_body (provider routing).
    text_eb = profile_for(model_id).text_extra_body
    if text_eb:
        overrides["extra_body"] = text_eb
    traced = tracing.tracing_on()
    # issue 0042: capture usage when tracing is on OR a usage scope is active (this is a per-call function, so the
    # scope entered around the invoke is visible here). `stream_usage` makes LC/OpenRouter emit usage_metadata on
    # the final chunk (issue 0017); the cost-capturing client recovers OpenRouter's real cost the chunk drops.
    capture = traced or usage_acct.usage_capturing()
    if capture:
        overrides["stream_usage"] = True
    # ISSUE-0021: use the cost-capturing client so OpenRouter's ACTUAL per-call cost (which LangChain's streaming
    # normalization drops) is recovered from the raw final chunk -- no more $0.00/UNPRICED for an unpriced model.
    client = build_model(model_id, temperature=temperature, _client_cls=_CostCapturingChatOpenAI, **overrides)
    usage: dict[str, Any] = {}
    ttft: list[Any] = []  # 0048: wall-clock of the FIRST content token (time to first token), for the queue/decode split
    gid: list[str] = []   # 0048: the provider generation id (OpenRouter) off the stream, for call attribution

    async def _consume() -> str:
        import datetime as _dt
        parts: list[str] = []
        async for chunk in client.astream(prompt):
            c = str(chunk.content)
            if c and not ttft:
                ttft.append(_dt.datetime.now(_dt.timezone.utc))
            if not gid and getattr(chunk, "id", None):
                gid.append(chunk.id)
            parts.append(c)
            um = getattr(chunk, "usage_metadata", None)
            if um:
                usage.update(um)
        return "".join(parts)

    # 0048: open the generation BEFORE the call so Langfuse's own latency is the real duration (not ~0).
    gen = tracing.start_generation(model=model_id, input=prompt, label=label, stage="astream_text") \
        if traced else None
    t0 = _time.monotonic()
    result = await _bounded_deadline(_consume, model_id, label)
    latency_ms = (_time.monotonic() - t0) * 1000.0
    inp, out = int(usage.get("input_tokens", 0) or 0), int(usage.get("output_tokens", 0) or 0)
    # cost is the provider's ACTUAL total (pass-through, like the litellm path); None if the backend/model did not
    # surface it (e.g. vLLM), so it counts as calls_without_cost / Langfuse prices from its table. getattr-guarded
    # so a substituted client (tests / a non-cost-capturing class) degrades to no cost rather than raising.
    cost = getattr(client, "_cost_holder", {}).get("cost")
    # issue 0042: record into any active usage scope (no-op if none), regardless of tracing.
    usage_acct.record_usage(model_id, input_tokens=inp, output_tokens=out, cost=cost, latency_ms=latency_ms)
    if gen is not None:  # 0048: end the generation opened above -> real span duration + time-to-first-token
        u = {"input": inp, "output": out} if usage else None
        tracing.finish_generation(gen, output=result, usage=u, cost=cost, latency_ms=latency_ms,
                                  completion_start_time=(ttft[0] if ttft else None),
                                  metadata=({"openrouter_generation_id": gid[0]} if gid else None))
    return result


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
