"""LG-0 observability: the vendor-neutral seam over the AMBIENT OpenTelemetry tracer.

Per GraphWright's observability contract (`temp/observability-contract.md`): we DO NOT create a tracer
provider, exporter, or Langfuse client, and we DO NOT initialize global tracing. GraphWright installs global
OTel instrumentation and exports to Langfuse (or ANY OTLP backend -- Phoenix/Jaeger/collector; the backend is
swappable with **zero change here**, which is the whole point). Our only jobs:

  1. Build every model through the LangChain seam (`models/seam.py` -> `ChatOpenAI`), so token counts + latency
     are captured automatically -- no code here for those.
  2. Instrument the ONE gap: **raw-SDK** calls that bypass LangChain (docling-graph / LiteLLM, sandboxed or
     out-of-band model calls) -- give them a span on the AMBIENT tracer with a model name + token usage.
  3. Optionally add domain **business spans** (retrieval stats, a custom segment) on the ambient tracer.
  4. Propagate the OTel context across any boundary WE introduce (separate process / worker / queue / custom
     async loop). Std-lib threads are carried by GraphWright's threading instrumentation.

This module is **dependency-free**: if opentelemetry is not importable (RAG_Wright standalone / hermetic
tests), every helper is a safe NO-OP; under GraphWright's runtime it attaches to the installed provider and
nests correctly. Never import Langfuse; never construct a provider. Ground OTel via the framework index /
docs when it is installed under GraphWright.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Optional

try:  # present under GraphWright's runtime; absent standalone -> everything below no-ops
    from opentelemetry import context as _context
    from opentelemetry import trace as _trace
    from opentelemetry.propagate import extract as _extract
    from opentelemetry.propagate import inject as _inject

    _OTEL = True
except Exception:  # noqa: BLE001 - opentelemetry not installed -> graceful no-op seam
    _OTEL = False

# OpenTelemetry GenAI semantic-convention attribute names (what OTLP backends read for token/cost views).
_GENAI_MODEL = "gen_ai.request.model"
_GENAI_SYSTEM = "gen_ai.system"
_GENAI_IN = "gen_ai.usage.input_tokens"
_GENAI_OUT = "gen_ai.usage.output_tokens"
_GENAI_TOTAL = "gen_ai.usage.total_tokens"


def otel_active() -> bool:
    """True when OpenTelemetry is importable (i.e. running under GraphWright's instrumented runtime)."""
    return _OTEL


def _tracer():
    return _trace.get_tracer("rag_wright.subgraphs") if _OTEL else None


@contextmanager
def business_span(name: str, **attributes: Any) -> Iterator[Any]:
    """A domain/business span on the AMBIENT tracer (never a new provider). No-op when OTel is absent.

    For domain steps / retrieval stats -- NOT for standard LangChain LLM/tool calls (already captured; a manual
    span would duplicate them).
    """
    tr = _tracer()
    if tr is None:
        yield None
        return
    with tr.start_as_current_span(name) as span:
        for key, value in attributes.items():
            span.set_attribute(key, value)
        yield span


@contextmanager
def raw_llm_span(name: str, *, model: str, system: Optional[str] = None) -> Iterator[Any]:
    """Instrument a RAW-SDK model call (the one gap: docling-graph/LiteLLM, sandboxed calls) on the ambient
    tracer, so it shows up in traces with a model name. Call `record_tokens(span, ...)` after the call for
    usage. Duration is the span's own. No-op when OTel is absent."""
    tr = _tracer()
    if tr is None:
        yield None
        return
    with tr.start_as_current_span(name) as span:
        span.set_attribute(_GENAI_MODEL, model)
        if system:
            span.set_attribute(_GENAI_SYSTEM, system)
        yield span


def record_tokens(
    span: Any,
    *,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    total_tokens: Optional[int] = None,
) -> None:
    """Record token usage on a raw-SDK span (the only counting you own; LangChain calls set these themselves).
    No-op on a None span."""
    if span is None:
        return
    if input_tokens is not None:
        span.set_attribute(_GENAI_IN, input_tokens)
    if output_tokens is not None:
        span.set_attribute(_GENAI_OUT, output_tokens)
    if total_tokens is not None:
        span.set_attribute(_GENAI_TOTAL, total_tokens)


def inject_context(carrier: dict) -> dict:
    """Producer side: serialize the current OTel context into `carrier` before crossing a boundary you
    introduce (separate process / external worker / queue / custom async). Returns the carrier. No-op without
    OTel. Std-lib threads do NOT need this (GraphWright instruments them)."""
    if _OTEL:
        _inject(carrier)
    return carrier


@contextmanager
def attach_context(carrier: dict) -> Iterator[None]:
    """Consumer side: re-attach a context carried across a boundary so the work nests under the original run.
    No-op without OTel."""
    if not _OTEL:
        yield
        return
    token = _context.attach(_extract(carrier))
    try:
        yield
    finally:
        _context.detach(token)
