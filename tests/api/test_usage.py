"""EP-API-5 (ADR-0117, ADR-0105): usage/cost on the engine API surface. A product measures an engine call's model
usage -- calls/tokens/cost/latency + a per-model breakdown -- by wrapping it in `api.measure_usage()`, with no reach
into engine internals (`rag_wright.models.usage`). Hermetic: `record_usage` stands in for a real model call."""
from __future__ import annotations

import pytest

from rag_wright.api import (
    EngineConfig,
    ModelUsage,
    StoreConfig,
    UsageTotals,
    WorkspaceHandle,
    ainvoke_subgraph,
    measure_usage,
)
from rag_wright.api import invoke as _invoke
from rag_wright.models import usage as _usage
from rag_wright.models.usage import record_usage


def _handle():
    return WorkspaceHandle(store=object(),
                           config=EngineConfig(store=StoreConfig(host="h", port="1", user="u", password="p")),
                           corpus="c")


def test_api_reexports_the_engine_usage_types():
    assert UsageTotals is _usage.UsageTotals and ModelUsage is _usage.ModelUsage


def test_measure_usage_captures_known_and_unknown_cost_calls():
    with measure_usage() as u:
        record_usage("qwen3.8-27b", input_tokens=120, output_tokens=40, cost=0.0012, latency_ms=850.0)
        record_usage("qwen3.8-27b", input_tokens=80, output_tokens=20, cost=None)  # backend surfaced no cost
    assert u.calls == 2
    assert u.input_tokens == 200 and u.output_tokens == 60
    assert u.cost_usd == pytest.approx(0.0012)
    assert u.calls_without_cost == 1  # unknown cost, NOT counted as $0
    assert u.latency_ms_total == pytest.approx(850.0)
    assert u.by_model["qwen3.8-27b"].calls == 2 and u.by_model["qwen3.8-27b"].output_tokens == 60


async def test_a_caller_scope_captures_an_invokes_usage(monkeypatch):
    """The product-facing contract: wrap the invoke in measure_usage() and read the model usage it incurred."""
    async def _stub(resources, inputs):
        record_usage("m", input_tokens=10, output_tokens=5, cost=0.01)
        return {"ok": True}

    monkeypatch.setattr(_invoke, "capability_impl", lambda name: _stub)  # EP-CORE-2: impl resolved via capability_impl
    with measure_usage() as u:
        out = await ainvoke_subgraph("relational_qa", {"q": 1}, resources=_handle())
    assert out == {"ok": True}
    assert u.calls == 1 and u.cost_usd == pytest.approx(0.01) and u.by_model["m"].output_tokens == 5


def test_recording_outside_any_scope_is_a_safe_no_op():
    record_usage("m", input_tokens=1, cost=0.0)  # no active scope -> recorded nowhere, must not raise
