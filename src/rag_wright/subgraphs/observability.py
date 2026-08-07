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

This module is a safe NO-OP unless a REAL tracer provider is installed (GraphWright's runtime): standalone /
hermetic tests, every helper no-ops; under GraphWright it attaches to the installed provider and nests
correctly. Activation hinges on a real provider being set, NOT on `opentelemetry` merely being importable --
the API can arrive as a transitive dependency (e.g. via FastMCP) with no SDK/provider configured, and that must
NOT flip instrumentation on. Never import Langfuse; never construct a provider. Ground OTel via the framework
index / docs when it is installed under GraphWright.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Optional

try:  # the API may be importable standalone (transitive dep) -> gate ACTIVATION on a real provider, not this
    from opentelemetry import context as _context
    from opentelemetry import trace as _trace
    from opentelemetry.propagate import extract as _extract
    from opentelemetry.propagate import inject as _inject

    _OTEL_IMPORTABLE = True
except Exception:  # noqa: BLE001 - opentelemetry not installed -> graceful no-op seam
    _OTEL_IMPORTABLE = False

# The default (unconfigured) providers the OTel API returns before GraphWright sets a real SDK provider. When
# the current provider is one of these, spans are non-recording, so every helper must no-op.
_NOOP_PROVIDER_TYPES = frozenset({"ProxyTracerProvider", "NoOpTracerProvider", "DefaultTracerProvider"})


def _provider_installed() -> bool:
    """True only when a REAL tracer provider is installed (GraphWright's instrumented runtime), not the default
    proxy/no-op the API ships with. This, not mere importability, is what activates the seam."""
    if not _OTEL_IMPORTABLE:
        return False
    return type(_trace.get_tracer_provider()).__name__ not in _NOOP_PROVIDER_TYPES

# OpenTelemetry GenAI semantic-convention attribute names (what OTLP backends read for token/cost views).
_GENAI_MODEL = "gen_ai.request.model"
_GENAI_SYSTEM = "gen_ai.system"
_GENAI_IN = "gen_ai.usage.input_tokens"
_GENAI_OUT = "gen_ai.usage.output_tokens"
_GENAI_TOTAL = "gen_ai.usage.total_tokens"


def otel_active() -> bool:
    """True when a real tracer provider is installed (i.e. running under GraphWright's instrumented runtime)."""
    return _provider_installed()


def _tracer():
    return _trace.get_tracer("rag_wright.subgraphs") if _provider_installed() else None


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
    if _provider_installed():
        _inject(carrier)
    return carrier


@contextmanager
def attach_context(carrier: dict) -> Iterator[None]:
    """Consumer side: re-attach a context carried across a boundary so the work nests under the original run.
    No-op without a real provider."""
    if not _provider_installed():
        yield
        return
    token = _context.attach(_extract(carrier))
    try:
        yield
    finally:
        _context.detach(token)
