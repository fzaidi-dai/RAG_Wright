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


def test_dispatch_model_rejects_an_unknown_name():
    import pytest

    # EP-CORE-2: dispatch resolves via the manifest impl_ref; 'embedding' is de-registered (EP-CORE-1a), so it is
    # unknown to the catalog -> KeyError.
    with pytest.raises(KeyError):
        mc.dispatch_model("embedding", {})


def test_the_two_model_capabilities_resolve_via_impl_ref():
    # EP-CORE-2: both model capabilities resolve through capability_impl (the manifest impl_ref), to the factories
    # in this module -- no central adapter dict.
    from rag_wright.capabilities.invoke import capability_impl

    assert capability_impl("clause_function_classification") is mc.clause_function_classification
    assert capability_impl("clause_property_classification") is mc.clause_property_classification
