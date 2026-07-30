"""LG-0 observability seam: dependency-free and NO-OP-safe when OpenTelemetry is absent.

RAG_Wright standalone (and hermetic tests) has no OTel installed -> every helper must be a safe no-op and
never construct a provider/exporter/Langfuse client. Under GraphWright's runtime (OTel present) the same
calls emit nested spans; that path is verified there, not here.
"""

from __future__ import annotations

from rag_wright.subgraphs import observability as obs
from rag_wright.subgraphs.scaffold import business_span as reexported_business_span


def test_otel_is_inactive_standalone():
    assert obs.otel_active() is False  # not running under GraphWright's instrumented runtime


def test_business_span_is_a_safe_noop():
    with obs.business_span("retrieval.rerank", candidate_count=25, model="bge") as span:
        assert span is None  # no provider -> no span, but no error and the body still runs


def test_raw_llm_span_and_record_tokens_are_safe_noops():
    with obs.raw_llm_span("dg_extraction.granite", model="ibm-granite/granite-4.1-8b") as span:
        assert span is None
        obs.record_tokens(span, input_tokens=100, output_tokens=50, total_tokens=150)  # no-op on None


def test_context_propagation_is_a_safe_noop():
    carrier: dict = {}
    assert obs.inject_context(carrier) is carrier
    assert carrier == {}  # nothing injected without OTel
    with obs.attach_context(carrier):
        pass  # no error


def test_scaffold_reexports_the_seam():
    with reexported_business_span("step"):
        pass  # importable + usable from the scaffold's single surface
