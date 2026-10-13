"""The agent's own chat model: which one, how it is bounded, and what it really cost (T-5a.4a-ii).

**The ENGINE builds it** (PS-36): `rag_wright.api.agent_chat_model` returns a chat model built from a model
profile, and what comes back already carries the
backend, endpoint, key and served id, the reasoning flag in that backend's own spelling, usage on streamed
calls, OpenRouter's real per-call cost, and a meter that records the call into the active usage scopes. It is
built with `trace=False`: `GenerationRecorder` below is the one emitter, so a call is traced once.

**What this module used to be is the point.** It held `DEFAULT_AGENT_MODEL = "openai:qwen/qwen3.8-27b"`, an
`is_openrouter()` helper, two hand-written `extra_body` dialects (OpenRouter's `reasoning` field and vLLM's
`chat_template_kwargs`), a provider pin, a `ProviderProfile` registration and a `CostTap` callback that
recovered the per-call cost LangChain's streaming normalisation drops. Every one of those is the engine's
job and every one of them is now gone rather than moved -- which is what made the engine publish their
builder before this stage. The vendor name, the endpoint choice and the cost model have left the substrate.

**What stays is the only part that was ever ours: the CLOCKS.** The timeout, the retry count and the two
deadlines below are measured against this harness's own latency distribution, and they are passed to the
engine as overrides. The engine's own advice is that retries belong in our middleware, which is where they
are (`harness/errors.py`).

**A provider pin is the PRODUCT's to make** (decided 2026-10-13). We dropped ours: engine ADR-0125 unpinned
these profiles after one endpoint's outage hung every call for its full deadline, and our
`deepinfra/bf16` + `allow_fallbacks=False` was that same configuration -- bounded here only by the
deadlines below, which the engine's pipeline did not have.

**And 0.9.0 gives that decision a clean home**: `ModelProfile` and `register_model_profile` are public now,
so a pin is a profile the PRODUCT registers and names by its own setting -- not a dict in the
substrate, and not a change to the engine's own profiles. If we ever want the endpoint pinned again, that
is the route, and it belongs in product code rather than here.

**Real cost, not a price table** (engine issue 0021), and the mechanism moved without the principle
changing. OpenRouter reports an actual per-call cost and LangChain's streaming normalisation drops it; the
engine's agent client keeps it on the chunk's `generation_info`. **A wrong cost is still worse than no
cost**: a deployment that bills by the second reports nothing, and the honest ledger state is *not
measured* (`priced=False`), never a per-token figure from Langfuse's table.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from rag_wright import api
from rag_wright.harness.tracing import open_generation, record_generation

logger = logging.getLogger(__name__)

#: How long ONE model call may take before it is abandoned.
#:
#: **There was no timeout at all, and that made every stall mechanism above it decorative.** Two runs hung
#: in one hour with the main thread blocked in `_ssl__SSLSocket_read` inside an asyncio task step -- the
#: provider had stalled mid-read, and the same model answered in 1.1s directly a minute later.
#: `asyncio.wait_for` cannot cancel a call like that: cancellation lands at an await point, so one run's
#: 150s ceiling fired 38 seconds late and another never fired until the socket itself gave up. A ceiling
#: is a promise the layer below has to be able to keep.
#:
#: **60 seconds is measured, not chosen.** Healthy agent turns in this harness run 1-25 seconds, including
#: the orchestrator's 24k-character system prompt; 60 is well past any of them and well short of a wait
#: a person would sit through.
MODEL_REQUEST_TIMEOUT_S = 60.0

#: How many times the SDK retries a stalled call **by itself**. Zero, deliberately.
#:
#: **Two retry layers multiplied a ceiling nobody was reading.** Retries belong to
#: `ModelRetryMiddleware` in `harness/errors.py`: it is ours, it is configured with a named `retry_on`,
#: its attempts are observable, and it is the one a reader finds. The SDK's own `max_retries` is
#: invisible from there and multiplies with it -- at `1`, each of the middleware's three attempts was
#: really two socket attempts, so the real ceiling on one turn was 360 seconds when the written-down
#: numbers said 60. Both observed stalls still recover on a fresh attempt; that retry now happens in
#: exactly one place.
MODEL_RETRIES = 0

#: The wall-clock ceiling on producing ONE model turn, retries and backoff included.
#:
#: **A socket timeout is not a wall clock, and that is why `MODEL_REQUEST_TIMEOUT_S` could not stop a
#: hang.** `httpx.Timeout(60.0)` sets connect, read, write and pool to 60 seconds each, and the *read*
#: timeout bounds the idle gap between bytes rather than total elapsed time. A provider that keeps the
#: connection warm while it thinks never trips it. httpx offers no total-elapsed option, so the only
#: place this can be imposed is above the transport -- which is what `ADeadlineForOneModelTurn` does.
#: A live `a_pair` run sat 445 seconds on one worker's FIRST turn with the 60s timeout correctly applied.
#:
#: **190 is the retry budget plus room, not a guess**: three attempts of `MODEL_REQUEST_TIMEOUT_S` plus
#: the backoff between them. A deadline below that would switch retries off while leaving them
#: configured, which `test_model_deadline.py` refuses.
MODEL_TURN_DEADLINE_S = 190.0

#: The wall clock on ONE attempt, and **sized from the latency distribution rather than the socket.**
#:
#: It used to be `MODEL_REQUEST_TIMEOUT_S`, which is a different measurement: that one is httpx's idle
#: gap between bytes, this one is elapsed time. Reusing 60s looked tidy and bought nothing -- three
#: attempts of 60s fill the 190s turn ceiling exactly, so a third hung attempt loses the run with no
#: room for a fourth.
#:
#: **It was 40s for an hour, and that was a regression -- recorded because the reasoning looked sound.**
#: 40s was calibrated on p90 (~15s) and the slowest COMPLETED call (38.5s) taken from Langfuse across all
#: agents. That is the wrong population: those are RAW call latencies, and a compiled worker's calls are
#: far slower than a raw one with the same model and tools. Bisected -- with our clocks disabled the same
#: worker completes in 133.8s / 142.4s / 201.0s, and with no middleware at all in ~127s, so the calls
#: were never hanging. They were slow, and a 40s clock abandoned every one of them and retried into the
#: turn ceiling. **Calibrate this against the COMPILED path, never against raw calls.**
MODEL_ATTEMPT_DEADLINE_S = 60.0


def agent_chat_model(settings: Any, model_id: str) -> Any:
    """The agent's chat model, built by the engine and bounded by our clocks.

    **`model_id` is a PARAMETER (T-7.58).** It used to be read from one product's setting name, in the
    substrate, so a second product's model could not be reached at all. Which profile to reason with is the product's
    declaration; bounding the call with our clocks is the substrate's job and is all that is left here.

    **No `ProviderProfile`, and that removed a trap rather than just a dependency.** The old path handed
    DeepAgents a spec STRING and registered a profile to configure it. `deepagents._models.resolve_model`
    returns a pre-built `BaseChatModel` *unchanged* and applies its profile registry only to strings --
    including, in its own words, "the OpenAI Responses API default". That default was ON, and with it on
    OpenRouter returns `object: "response"` and reports **no cost at all**, which is why this module
    carried `use_responses_api=False`. Passing an object retires the counter-measure with its cause.

    **It also retires a latent bug.** The old `agent_chat_model` registered the profile under
    `agent_model_spec(settings)` and then resolved the `AGENT_MODEL_SPEC` constant -- so configuring a
    different model registered the profile under one string and asked for another, which is the exact
    failure the registration's own comment warned about ("a profile registered under one string while the
    agent asks for another ... resolves with no api_key, no base_url and no reasoning flag"). It was masked
    only because the setting's default equalled the constant.
    """
    # **The credentials reach the engine through its ENVIRONMENT** (the model profile names the key variable);
    # setting it is the product's job, before the run. Naming the provider is what the substrate must not do.
    # `trace=False`: the engine meter still meters; `GenerationRecorder` emits the generation.
    model = api.agent_chat_model(
        model_id,
        trace=False,
        timeout=MODEL_REQUEST_TIMEOUT_S,
        max_retries=MODEL_RETRIES,
    )
    _disable_the_catch_all_subagent(model)
    return model


def _disable_the_catch_all_subagent(model: Any) -> None:
    """Take away DeepAgents' automatic `general-purpose` worker.

    **It mirrors whichever agent it is attached to** -- the docs describe it as having *"access to all
    tools as the main agent"* -- so it is a route around every specialist worker that gains nothing but a
    hop. Our tiers exist to isolate context: an orchestrator that can delegate to a copy of itself is the
    tier structure with the point removed.

    **`task` survives**, which is the thing worth checking: `SubAgentMiddleware` attaches only when at
    least one synchronous subagent exists, so disabling the catch-all would remove delegation entirely from
    an agent that had no workers of its own. Both our tiers have workers, so both keep `task`.

    **The documented path, and the wrong one is tempting.** The docs are explicit: *"Don't reach for
    `excluded_middleware` here -- `SubAgentMiddleware` is required scaffolding and listing it raises
    `ValueError`. The `general_purpose_subagent.enabled = False` knob is the supported path."*

    **Keyed off the BUILT MODEL, which is the change this stage forced.** The registry is keyed by model,
    and we used to key it with our own spec constant. With the engine building the model there is no spec
    string to use, and hardcoding one would put a vendor slug back in this module -- worse, it would key a
    profile that `_harness_profile_for_model` might not match, silently restoring the catch-all. So the key
    is derived from the model the way DeepAgents derives it for a pre-built instance: `provider:identifier`.

    Reaching into `deepagents._models` is deliberate and guarded, for the same reason the old code reached
    there for `resolve_model`: it is the function `create_deep_agent` itself uses, so deriving the key any
    other way would let the agent and this registration disagree. `test_harness_model.py` asserts the
    symbols still exist, so a version bump fails loudly instead of diverging quietly.
    """
    from deepagents import (
        GeneralPurposeSubagentProfile,
        HarnessProfileConfig,
        register_harness_profile,
    )
    from deepagents._models import get_model_identifier, get_model_provider

    register_harness_profile(
        f"{get_model_provider(model)}:{get_model_identifier(model)}",
        HarnessProfileConfig(general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)),
    )


def real_cost_of(response: LLMResult) -> float | None:
    """The provider's actual cost for one completed call, or `None` when it did not report one.

    Looked for where OpenRouter actually puts it, in both shapes a response can take: `llm_output` for a
    non-streamed call, and the final generation's `generation_info` for a streamed one.
    """
    usage = (response.llm_output or {}).get("token_usage") or (response.llm_output or {}).get("usage") or {}
    cost = usage.get("cost")

    if cost is None:
        for batch in response.generations:
            for generation in batch:
                info = getattr(generation, "generation_info", None) or {}
                cost = (info.get("usage") or {}).get("cost", info.get("cost"))
                if cost is not None:
                    break
    return float(cost) if cost is not None else None


def generation_id_of(response: LLMResult) -> str | None:
    """OpenRouter's id for one completed call, or `None` when there is not one.

    **It is the id, not the provider, and that distinction cost a wrong instrument.** The first version
    of this read `provider` -- which OpenRouter does return, at the TOP LEVEL of the response body, and
    which the OpenAI-compatible client drops entirely. Measured: `llm_output` carries only `model_name`
    and `token_usage`; `response_metadata` adds `id`, `finish_reason`, `system_fingerprint` (`None`
    here) and `model_provider` (`'openai'` -- langchain's label for the client class, not the endpoint).
    So the field was unreachable and the helper returned `None` forever while looking like it worked.
    That is the defect this session kept finding, written by me, in the instrument meant to catch it.

    The id IS reachable, and it is enough: `GET /api/v1/generation?id=...` reports the serving endpoint
    for any call, after the fact and without paying for it inline.

    **The pin itself does not depend on this.** `order=["deepinfra/bf16"]` with `allow_fallbacks=False`
    is enforced by the API -- verified: a bogus slug returns `404 No endpoints found` rather than
    quietly serving from somewhere else. This is forensics, not the guarantee.
    """
    for batch in response.generations:
        for generation in batch:
            metadata = getattr(getattr(generation, "message", None), "response_metadata", None) or {}
            found = metadata.get("id")
            if found:
                return str(found)
    return None


def token_usage_of(response: LLMResult) -> dict[str, int]:
    """Input and output token counts, in the shape Langfuse's `usage_details` wants."""
    usage = (response.llm_output or {}).get("token_usage") or {}
    prompt = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
    completion = usage.get("completion_tokens") or usage.get("output_tokens") or 0

    if not (prompt or completion):
        for batch in response.generations:
            for generation in batch:
                meta = getattr(getattr(generation, "message", None), "usage_metadata", None) or {}
                prompt = prompt or meta.get("input_tokens", 0)
                completion = completion or meta.get("output_tokens", 0)
    return {"input": int(prompt), "output": int(completion)}


#: What `lc_agent_name` being `None` means: the agent the caller invoked, rather than missing data.
THE_TOP_TIER = "the tier invoked directly"


def _as_recorded(message: Any) -> dict[str, Any]:
    """One message, in a shape a person can read back weeks later.

    **Tool calls keep their ARGUMENTS.** "It called `ask_matter_question` 23 times" is a symptom; the 23
    argument values are the diagnosis, and they are what says whether a worker was rephrasing one question
    or asking twenty-three different ones.
    """
    content = getattr(message, "content", message)
    if isinstance(content, list):
        content = " ".join(block.get("text", "") if isinstance(block, dict) else str(block)
                           for block in content)
    recorded: dict[str, Any] = {"role": getattr(message, "type", "?"), "text": str(content or "")}
    calls = getattr(message, "tool_calls", None) or []
    if calls:
        recorded["tool_calls"] = [{"name": call["name"], "args": call.get("args", {})} for call in calls]
    named = getattr(message, "name", None)
    if named:
        recorded["name"] = named
    return recorded


class GenerationRecorder(BaseCallbackHandler):
    """Emit one Langfuse generation per completed model call — the engine's shape, for our calls.

    **Why we emit rather than letting Langfuse's LangChain handler do it.** That handler creates
    observations with `start_observation` and never `start_as_current_observation`, so its generations are
    never the current span and a real cost cannot be attached to them from anywhere. The engine hand-rolls
    `record_generation` for exactly that reason; this is the same decision reached the same way, after
    trying the framework-native route and watching the cost vanish.

    **It works for both cost bases, which is the point of the `cost_basis` tag.**

    | backend | what it reports | what lands |
    |---|---|---|
    | OpenRouter | actual per-call cost | `cost_details`, a pass-through |
    | self-hosted vLLM / Modal | nothing — billed by GPU-second | latency, tagged `per-second` |

    A generation tagged `per-second` carries no cost and a real latency, so a rate applied later turns one
    into the other. Without the tag, an unpriced run is indistinguishable from a free one.

    **Never breaks a call.** Telemetry degrades to silence.
    """

    def __init__(self, settings: Any, model_id: str | None = None) -> None:
        self._settings = settings
        #: **Told, not derived** (T-7.58). This called `agent_model_id(settings)` at four points, which
        #: put one product's setting name in the substrate four times over.
        #:
        #: **Optional, because the completed call reports its own model name and that is the better
        #: label anyway.** `on_llm_end` prefers `response.llm_output["model_name"]`; this is the fallback
        #: for the opening span, where nothing has come back yet. A caller that wants the profile id in
        #: the trace from the first byte passes it; the substrate itself does not know it.
        self._model_id = model_id
        self._started: dict[Any, float] = {}
        self._asked: dict[Any, list[dict[str, Any]]] = {}
        self._by: dict[Any, str] = {}
        #: The open observation per in-flight call, so its duration is the call's rather than zero.
        self._spans: dict[Any, Any] = {}
        #: The model turn each open call belongs to (its parent run). Retries of one turn share it; a concurrent
        #: worker's call does not (PS-32: measured on a retried turn beside a parallel agent).
        self._turn: dict[Any, Any] = {}
        #: Kept for tests and for a caller wanting the figure without reading Langfuse back.
        self.costs: list[float] = []

    def on_llm_start(self, serialized: dict, prompts: list[str], *, run_id: Any = None,
                     parent_run_id: Any = None, **kwargs: Any) -> None:
        # **Both start hooks open a span, not just the chat one.** Our tiers drive CHAT models, so
        # `on_chat_model_start` is the hook production takes -- but a plain-LLM call that came through
        # here would otherwise fall back to the after-the-fact path and be the only zero-duration row
        # left in the trace, which is exactly the kind of gap that misleads a reader (T-5a.12ad).
        self._close_replaced_attempts(parent_run_id)
        self._started[run_id] = time.monotonic()
        self._turn[run_id] = parent_run_id
        self._spans[run_id] = open_generation(self._settings,
                                             model=self._model_id,
                                             input=prompts)

    def on_chat_model_start(self, serialized: dict, messages: Any, *, run_id: Any = None,
                            parent_run_id: Any = None, metadata: dict[str, Any] | None = None,
                            **kwargs: Any) -> None:
        """**What the model was actually asked, and by which tier.**

        The first version aliased this to `on_llm_start`, which takes `prompts` -- so the messages arrived
        and were discarded, and even at `verbose` the trace carried the reply and never the prompt. When a
        worker took `ask_matter_question` 23 times under the orchestrator against 3 alone, nothing could
        say what it asked on those turns, and the cause had to be guessed at from tool counts.

        `metadata["lc_agent_name"]` names the subagent and is `None` for the tier invoked directly --
        confirmed by running a real two-tier agent, because a trace in which every row says `agent` cannot
        tell an orchestrator turn from a worker turn.
        """
        # **A span still open for THIS turn is an attempt that was cancelled** (T-5a.14b): a completed call
        # closes its own in `on_llm_end`, a failed one in `on_llm_error`, and a cancelled one closes nothing,
        # because cancellation is a `BaseException` and reaches neither callback. **Only this turn's** (PS-32):
        # one recorder serves the whole run, so a parallel worker's call is open at the same time and is not
        # abandoned because another call started.
        self._close_replaced_attempts(parent_run_id)
        self._started[run_id] = time.monotonic()
        self._turn[run_id] = parent_run_id
        self._by[run_id] = (metadata or {}).get("lc_agent_name") or THE_TOP_TIER
        turn = messages[0] if messages and isinstance(messages[0], list) else (messages or [])
        self._asked[run_id] = [_as_recorded(message) for message in turn]
        # **Opened HERE so the observation spans the call** (T-5a.12ad). Emitting the whole thing in
        # `on_llm_end` gave every generation a start equal to its end, so a 257s call and a 2s call were
        # indistinguishable in Langfuse and the latency lived only in metadata.
        self._spans[run_id] = open_generation(self._settings, model=self._model_id,
                                              input=self._asked[run_id])

    def _close_replaced_attempts(self, turn: Any) -> None:
        """Close the open spans of earlier attempts at the same model turn: a new attempt means they were cancelled.
        A call with no parent run is not a retry of anything, so it closes nothing (the end-of-run sweep does)."""
        if turn is None:
            return
        for run_id in [held for held, of in self._turn.items() if of == turn and held in self._spans]:
            self._close_one(run_id)

    def close_abandoned(self) -> None:
        """Close every span this recorder opened and never finished (T-5a.14b).

        **An abandoned attempt is work, and it was invisible.** When `ADeadlineForOneModelTurn` cancels a
        model call, the cancellation is a `BaseException`, so neither `on_llm_end` nor `on_llm_error`
        fires -- driving the real middleware produces `['start', 'start', 'start']` and nothing else. The
        span opened at `on_chat_model_start` was simply never closed, so the attempt vanished from the
        trace.

        **It read as DEAD AIR, which is worse than a missing row**: it looks like the system doing
        nothing. The routing eval showed 61s and 60s "gaps" that were abandoned 60s attempts, and they
        were attributed in turn to the provider, to a stall and to the eval before being measured. A run
        that spends half its wall clock on attempts nobody records cannot be reasoned about at all.

        Called at the next attempt (the common case) and at the end of the run (for the last one, which
        is the attempt a failed run died on).
        """
        for run_id in list(self._spans):
            self._close_one(run_id)

    def _close_one(self, run_id: Any) -> None:
        self._turn.pop(run_id, None)
        span = self._spans.pop(run_id, None)
        started = self._started.pop(run_id, None)
        by = self._by.pop(run_id, None)
        self._asked.pop(run_id, None)
        if span is None:
            return
        # **`open_for_ms`, NOT `latency_ms`, and the distinction is the whole point.** This recorder
        # learns of a cancellation only when the NEXT attempt opens, so the elapsed time it can measure
        # runs from start to DISCOVERY -- which includes the retry backoff. Measured: 1.0s attempts
        # recorded as 2.0s and 3.0s. Calling that `latency_ms` would put an inflated number in the field
        # every reader sums, which is the same class of defect as the zero-duration bug it replaced.
        # An upper bound named as an upper bound is useful; a wrong latency is worse than none.
        open_for_ms = (time.monotonic() - started) * 1000.0 if started is not None else None
        try:
            # **Marked, not merely closed.** A closed-but-unlabelled span reads as a successful call that
            # produced nothing, which would be counted as work -- the opposite error, and a worse one.
            span.update(metadata={"abandoned": True, "open_for_ms": open_for_ms,
                                  "open_for_is_an_upper_bound": True,
                                  "agent": by or THE_TOP_TIER, "role": "agent"},
                        level="WARNING", status_message="abandoned before it returned")
            span.end()
        except Exception:
            logger.warning("harness_abandoned_attempt_emit_failed", exc_info=True)

    def on_llm_end(self, response: LLMResult, *, run_id: Any = None, parent_run_id: Any = None,
                   **kwargs: Any) -> None:
        # **A span still open for this turn belongs to an attempt that was cancelled**, not to this call: a
        # completed call always closes its own. Only this turn's (PS-32): a parallel worker's call may still be
        # in flight. The end-of-run sweep closes anything else left open.
        self._turn.pop(run_id, None)
        self._close_replaced_attempts(parent_run_id)
        started = self._started.pop(run_id, None)
        asked = self._asked.pop(run_id, None)
        by = self._by.pop(run_id, None)
        span = self._spans.pop(run_id, None)
        latency_ms = (time.monotonic() - started) * 1000.0 if started is not None else None

        cost = real_cost_of(response)
        if cost is not None:
            self.costs.append(cost)

        try:
            record_generation(
                self._settings,
                model=(response.llm_output or {}).get("model_name") or self._model_id,
                # **Gated on `verbose` inside `record_generation`, and it stays that way.** Prompts are a
                # customer's content; supplying one here does not widen where it is sent.
                input=asked,
                output=response.generations[0][0].text if response.generations else None,
                usage=token_usage_of(response),
                cost=cost,
                latency_ms=latency_ms,
                generation=span,
                metadata={
                    "role": "agent",
                    # **The handle that makes a call answerable later** (T-5a.12w). OpenRouter's
                    # serving endpoint is not reachable through this client, but its generation id is,
                    # and `/api/v1/generation?id=...` reports the endpoint for any id.
                    "openrouter_generation_id": generation_id_of(response),
                    # **Which tier spoke.** Without it every row in the trace says `agent`.
                    "agent": by,
                    # **The basis, not just the figure.** `per-call` means the number is the provider's
                    # own; `per-second` means there is no per-call number and the latency is the input a
                    # rate is applied to later.
                    #
                    # **Read off the OBSERVATION, not off the endpoint URL** (T-7.58 stage 2). This used to
                    # be `is_openrouter(base_url)`, which inferred the basis from a vendor name in the
                    # configured URL -- and inferred it WRONG whenever OpenRouter reported nothing, which it
                    # does when `usage.include` is missing. A reported cost IS the per-call basis; its
                    # absence IS the per-second one. The honest answer was always in `cost`.
                    "cost_basis": "per-call" if cost is not None else "per-second",
                },
            )
        except Exception:
            logger.warning("harness_generation_record_failed", exc_info=True)

    def on_llm_error(self, error: BaseException, *, run_id: Any = None, **kwargs: Any) -> None:
        """**A call that FAILED is closed as an error, not left for the abandoned sweep** (PS-32). Left open, the
        retry that follows closed it as "abandoned before it returned", which is a different thing: it did return,
        with an error, and the error's type is what a reader needs."""
        self._turn.pop(run_id, None)
        span = self._spans.pop(run_id, None)
        started = self._started.pop(run_id, None)
        by = self._by.pop(run_id, None)
        self._asked.pop(run_id, None)
        if span is None:
            return
        try:
            latency_ms = (time.monotonic() - started) * 1000.0 if started is not None else None
            span.update(metadata={"error": type(error).__name__, "latency_ms": latency_ms,
                                  "agent": by or THE_TOP_TIER, "role": "agent"},
                        level="ERROR", status_message=str(error)[:300])
            span.end()
        except Exception:
            logger.warning("harness_failed_call_emit_failed", exc_info=True)

