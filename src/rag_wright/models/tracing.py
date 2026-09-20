"""Engine LLM tracing to Langfuse (engine issue 0017).

The engine owns its model calls, so it owns their instrumentation: one Langfuse **generation** per model call,
carrying model id, token usage, latency, and the semantic metadata only the engine holds (the ADR-0058 `label`
plus role/stage and any caller correlation id). The product queries Langfuse directly -- this module is an
EMISSION contract, never a read API.

Gating (two independent conditions, BOTH required to emit):
  1. `RAG_TRACE_LEVEL` in {generations, verbose} (default `off`).
  2. `LANGFUSE_*` credentials actually configured.
Activation hinges on real CONFIGURATION, never on importability -- langfuse (and its transitive opentelemetry-sdk)
being installed says nothing about whether the operator wants telemetry (the FastMCP/OTel lesson, restated by the
issue). `generations` emits model+usage+latency+metadata; `verbose` also captures the input prompt and output.

Tracing must NEVER break a model call: every langfuse touch is wrapped and degrades to a no-op on any error.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator, Optional

_LEVELS = ("off", "generations", "verbose")


def trace_level() -> str:
    lvl = os.environ.get("RAG_TRACE_LEVEL", "off").strip().lower()
    return lvl if lvl in _LEVELS else "off"


def _configured() -> bool:
    """Gate on REAL config -- the credentials -- not on `import langfuse` succeeding."""
    return bool(os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"))


def tracing_on() -> bool:
    return trace_level() != "off" and _configured()


_client: Any = None
_client_tried = False


def _get_client() -> Any:
    """Lazy process-lifetime Langfuse client, constructed only when tracing is on AND configured. Constructing it
    initializes langfuse's OWN OpenTelemetry provider -- we never touch the engine's dormant observability seam."""
    global _client, _client_tried
    if _client is not None or _client_tried:
        return _client
    _client_tried = True
    if not tracing_on():
        return None
    try:
        from langfuse import Langfuse
        _client = Langfuse()  # reads LANGFUSE_PUBLIC_KEY / SECRET_KEY / HOST from env
    except Exception:  # noqa: BLE001 - tracing must never break the engine
        _client = None
    return _client


def start_generation(*, model: str, input: Any = None, label: Optional[str] = None,
                     role: Optional[str] = None, stage: Optional[str] = None,
                     metadata: Optional[dict[str, Any]] = None) -> Any:
    """Open a Langfuse generation AT THE CALL START and return the observation (or None when tracing is off).
    Its `start_time` is the moment this is called, so pairing it with `finish_generation` after the call makes
    the span's OWN duration the real wall-clock latency. This matters because langfuse v4 has no way to
    back-date a start: a post-hoc emit (open + immediately end) reports a ~0s duration and the real figure
    survives only in metadata -- the defect engine issue 0048 hit (Langfuse's latency column read 0 for
    `astream_text`/`span-relevance`). `input` is captured only at `verbose`; label/role/stage go into metadata.
    Document/job grouping comes from the ambient `traced_run`."""
    lf = _get_client()
    if lf is None:
        return None
    verbose = trace_level() == "verbose"
    md = {"label": label, "role": role, "stage": stage, **(metadata or {})}
    md = {k: v for k, v in md.items() if v is not None}
    try:
        return lf.start_observation(
            name=label or stage or "llm", as_type="generation",
            input=input if verbose else None, model=model, metadata=md or None,
        )
    except Exception:  # noqa: BLE001 - never let tracing break a model call
        return None


def finish_generation(gen: Any, *, output: Any = None, usage: Optional[dict[str, int]] = None,
                      cost: Optional[float] = None, latency_ms: Optional[float] = None,
                      completion_start_time: Any = None,
                      metadata: Optional[dict[str, Any]] = None) -> None:
    """Attach a completed call's results to a generation opened by `start_generation` and END it (end_time =
    now), so the observation's duration is the true latency. No-op when `gen` is None (tracing off). `output`
    captured only at `verbose`; `usage` = {"input": n, "output": n}; `cost` is the provider's ACTUAL total USD
    (OpenRouter pass-through, not an engine price table) -> `cost_details` (None when the backend omits it, e.g.
    self-hosted vLLM, so Langfuse prices from its own table). `completion_start_time` (time to first token) lets
    Langfuse split queue+prefill from decode -- the attribution engine issue 0048 asked for. `latency_ms` is
    also kept in metadata (redundant with the now-correct span duration, but exact)."""
    if gen is None:
        return
    verbose = trace_level() == "verbose"
    late = {"latency_ms": latency_ms, **(metadata or {})}
    late = {k: v for k, v in late.items() if v is not None}
    try:
        gen.update(output=output if verbose else None, usage_details=usage or None,
                   cost_details=({"total": cost} if cost is not None else None),
                   completion_start_time=completion_start_time, metadata=late or None)
        gen.end()
    except Exception:  # noqa: BLE001 - never let tracing break a model call
        pass


def record_generation(*, model: str, input: Any = None, output: Any = None,
                      usage: Optional[dict[str, int]] = None, cost: Optional[float] = None,
                      latency_ms: Optional[float] = None, label: Optional[str] = None,
                      role: Optional[str] = None, stage: Optional[str] = None,
                      metadata: Optional[dict[str, Any]] = None) -> None:
    """Post-hoc convenience: open + immediately end a generation. The measured span duration is ~0 (the real
    latency survives only in metadata) -- PREFER `start_generation`/`finish_generation` around the call so
    Langfuse's own latency is correct (issue 0048). Kept for a caller that genuinely has only post-call data."""
    gen = start_generation(model=model, input=input, label=label, role=role, stage=stage, metadata=metadata)
    finish_generation(gen, output=output, usage=usage, cost=cost, latency_ms=latency_ms)


@contextmanager
def traced_step(name: str, *, metadata: Optional[dict[str, Any]] = None) -> Iterator[None]:
    """Time a NON-generation sub-step (e.g. retrieval: ArcadeDB + embedding + rerank) as its OWN Langfuse span,
    so its duration is separable from the generation in the same trace -- the retrieval/generation split engine
    issue 0048 asked for. No-op unless tracing is on. (Distinct from `subgraphs.observability.business_span`,
    which is an OTel-ambient span that no-ops under a Langfuse-only setup -- this one emits to Langfuse.)"""
    lf = _get_client()
    if lf is None:
        yield
        return
    obs = None
    try:
        obs = lf.start_observation(name=name, as_type="span", metadata=metadata or None)
    except Exception:  # noqa: BLE001 - never let tracing break the step
        obs = None
    try:
        yield
    finally:
        if obs is not None:
            try:
                obs.end()
            except Exception:  # noqa: BLE001
                pass


@contextmanager
def traced_run(*, document_id: Optional[str] = None, job_id: Optional[str] = None,
               name: Optional[str] = None, metadata: Optional[dict[str, Any]] = None) -> Iterator[None]:
    """Group every generation emitted inside the block under one trace/session -- the caller's correlation id
    (job/document). A no-op unless tracing is on. Flushes on exit so a short-lived run's spans are sent."""
    if not tracing_on():
        yield
        return
    cm = None
    try:
        from langfuse import propagate_attributes
        md = {"document_id": document_id, **(metadata or {})}
        md = {k: v for k, v in md.items() if v is not None}
        cm = propagate_attributes(session_id=job_id or document_id, metadata=md or None, trace_name=name)
        cm.__enter__()
    except Exception:  # noqa: BLE001
        cm = None
    try:
        yield
    finally:
        if cm is not None:
            try:
                cm.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
        lf = _get_client()
        if lf is not None:
            try:
                lf.flush()
            except Exception:  # noqa: BLE001
                pass
