"""Langfuse for the harness's own model calls (T-5a.4a, ANFR-3, NFR-3).

**In the engine (PS-32) this module emits through the engine's tracing on `rag_wright.api`**: one Langfuse client,
one configuration (`RAG_TRACE_LEVEL` = `generations` or `verbose`, AND `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`),
the same switch every engine generation obeys. The harness builds its model with `agent_chat_model(..., trace=False)`,
so the engine meter only meters and `GenerationRecorder` is the one emitter: a call is traced once, never twice.
`settings` stays in the signatures as the caller's "account this run" handle; nothing here reads a field of it.

The harness's model calls -- every turn of tool-calling the agent does -- are its own, so it records them: a
reader correlating spend by the run's session id sees every agent turn, not only the engine calls inside tools.

Four rules taken from the engine verbatim, each because it was learned the hard way:

1. **Gate on trace level AND real credentials.** *"Activation hinges on real CONFIGURATION, never on
   importability."* `langfuse` pulls in `opentelemetry-api` here exactly as `fastmcp` once did in the
   engine; a feature that switches on because a package is installed switches on for people who never
   asked for telemetry.
2. **One session per run**, via `propagate_attributes`. `usage_since(session_ids=…)` correlates on exactly
   this. Without it the reader falls back to summing a time window, which attributes anything else running
   at that moment to this caller — unusable the moment two people ask a question at once.
3. **Flush on exit**, because a short-lived run's spans are otherwise still in the exporter when the
   reader looks. *"Flush is not arrival"* — the reader then waits for them to land.
4. **Tracing must never break a model call.** Every Langfuse touch is wrapped and degrades to a no-op.

**We emit each generation by hand, exactly as the engine does, and the first design did not.** Langfuse's
official LangChain handler looked like the framework-native choice and it cannot carry a real cost: it
creates observations with `start_observation` and never `start_as_current_observation`, so its generations
are never the current span and nothing can attach a cost to them afterwards. Eight of the first, none of
the second, in the installed version. That is why the engine hand-rolls this, and why we now do too — the
standing principle cuts the other way here: replicate the engine's mechanism where it makes sense.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from rag_wright import api

logger = logging.getLogger(__name__)


def harness_tracing_on(settings: Any = None) -> bool:
    """The engine's switch: a trace level other than `off` AND real Langfuse credentials, both required."""
    return api.tracing_on()


def open_generation(settings: Any, *, model: str | None, input: Any = None) -> Any:
    """Open the observation BEFORE the model call, so its duration is the call's (T-5a.12ad).

    **An observation created after the call has a duration of zero**, so a 900s run's generations each looked
    instant. Returns the observation, or `None` when tracing is off or Langfuse is unreachable; `None` is a normal
    value: `record_generation` then emits a complete observation, so a call never seen starting is still recorded.
    The input is captured only at `verbose` (the engine's rule).
    """
    try:
        if not api.tracing_on():
            return None
        return api.start_generation(model=model or "agent", input=input, label="agent-llm")
    except Exception:
        logger.warning("harness_generation_open_failed", exc_info=True)
        return None


def record_generation(
    settings: Any,
    *,
    model: str | None,
    input: Any = None,
    output: Any = None,
    usage: dict[str, int] | None = None,
    cost: float | None = None,
    latency_ms: float | None = None,
    metadata: dict[str, Any] | None = None,
    generation: Any = None,
) -> None:
    """Close ONE Langfuse generation for a COMPLETED model call (or emit a complete one). A no-op unless tracing is on.

    **`cost` is a PASS-THROUGH of what the provider reported, never a price table**; `None` means the backend did
    not report one. **`metadata` carries the cost BASIS**, which makes a per-second deployment apportionable.
    **`input` and `output` are captured only at `verbose`.** At `verbose` each generation is flushed as it ends,
    so a stalled run is visible while it stalls.
    """
    try:
        if not api.tracing_on():
            return
        if generation is None:
            # The fallback, and it must stay: a dropped row reads as a call that never happened.
            generation = api.start_generation(model=model or "agent", input=input, label="agent-llm")
        if generation is None:
            return
        if model:
            # Set at the end: the provider reports what actually served the call.
            generation.update(model=model)
        api.finish_generation(generation, output=output,
                              usage=usage or None, cost=cost, latency_ms=latency_ms, metadata=metadata)
        if api.trace_level() == "verbose":
            api.flush_tracing()
    except Exception:
        logger.warning("harness_generation_emit_failed", exc_info=True)


@contextmanager
def traced_agent_run(
    settings: Any,
    *,
    session_id: str,
    name: str = "agent-run",
    metadata: dict[str, Any] | None = None,
) -> Iterator[None]:
    """Group every generation emitted inside the block under one trace and session, the RUN id, and flush on exit.

    The engine's `traced_run`, keyed by the run id, which is what makes an agent run measurable: a reader handed
    that id gets this run's spend rather than a window's. A no-op unless tracing is on; never breaks the run.
    """
    with api.traced_run(job_id=session_id, name=name, metadata=metadata):
        yield
