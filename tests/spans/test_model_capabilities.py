"""EP-RT-7 (ADR-0117): the single model-capability binding + the guardrail that the ingestion pipeline's classify
stages route THROUGH it (never a second hand-built fleet -- see memory route-production-through-capability-layer).

Hermetic: `dispatch_model` is stubbed so no fleet loads; we assert the pipeline's classify shims dispatch by the
capability name with the right inputs."""
from __future__ import annotations

import asyncio

from rag_wright.spans import model_capabilities as mc


def test_function_classifier_shim_routes_through_the_capability(monkeypatch):
    calls = []
    monkeypatch.setattr(mc, "dispatch_model", lambda name, inputs: calls.append((name, inputs)) or [[]])
    mc.CapabilityFunctionClassifier().classify_spans("chunk text", ["span a", "span b"])
    assert calls == [("clause_function_classification", {"chunk_text": "chunk text", "span_texts": ["span a", "span b"]})]


def test_function_classifier_async_shim_routes_through_the_capability(monkeypatch):
    calls = []
    monkeypatch.setattr(mc, "dispatch_model", lambda name, inputs: calls.append((name, inputs)) or [[]])
    asyncio.run(mc.CapabilityFunctionClassifier().aclassify_spans("ct", ["s"]))
    assert calls == [("clause_function_classification", {"chunk_text": "ct", "span_texts": ["s"]})]


def test_property_classifier_fn_routes_through_the_capability(monkeypatch):
    calls = []
    monkeypatch.setattr(mc, "dispatch_model",
                        lambda name, inputs: calls.append((name, inputs)) or [{"dimension": "d", "value": "v", "confidence": "EXTRACTED"}])
    out = mc.capability_property_classifier_fn()("some clause text", ("Cap On Liability",))
    assert out == [{"dimension": "d", "value": "v", "confidence": "EXTRACTED"}]
    assert calls == [("clause_property_classification", {"text": "some clause text", "functions": ("Cap On Liability",)})]


def test_dispatch_model_rejects_an_unwired_name():
    import pytest

    with pytest.raises(NotImplementedError):
        mc.dispatch_model("embedding", {})  # a model-kind slug with no adapter wired


def test_the_wired_model_adapters_are_the_two_classifiers():
    assert set(mc.model_adapter_names()) == {"clause_function_classification", "clause_property_classification"}
