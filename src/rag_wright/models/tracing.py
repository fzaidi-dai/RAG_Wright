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


def record_generation(*, model: str, input: Any = None, output: Any = None,
                      usage: Optional[dict[str, int]] = None, latency_ms: Optional[float] = None,
                      label: Optional[str] = None, role: Optional[str] = None, stage: Optional[str] = None,
                      metadata: Optional[dict[str, Any]] = None) -> None:
    """Emit ONE Langfuse generation for a COMPLETED model call. No-op unless tracing is on + configured.
    `input`/`output` are captured only at `verbose`; `usage` = {"input": n, "output": n} token counts; `latency_ms`
    and label/role/stage go into metadata (queryable). Grouping to a document/job comes from the ambient
    `traced_run` (langfuse `propagate_attributes`), so this call carries no correlation id itself."""
    lf = _get_client()
    if lf is None:
        return
    verbose = trace_level() == "verbose"
    md = {"label": label, "role": role, "stage": stage, "latency_ms": latency_ms, **(metadata or {})}
    md = {k: v for k, v in md.items() if v is not None}
    try:
        gen = lf.start_observation(
            name=label or stage or "llm", as_type="generation",
            input=input if verbose else None, model=model, metadata=md or None,
        )
        gen.update(output=output if verbose else None, usage_details=usage or None)
        gen.end()
    except Exception:  # noqa: BLE001 - never let tracing break a model call
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
