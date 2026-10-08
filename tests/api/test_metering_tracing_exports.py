"""PS-3 (G18): a product meters its OWN model calls into the engine's usage scopes (`record_usage`) and correlates
its runs and steps with the engine's traces (`traced_run`, `traced_step`), all from `rag_wright.api`."""
from __future__ import annotations

from rag_wright.api import measure_usage, record_usage, traced_run, traced_step


def test_a_product_call_is_metered_into_every_active_scope():
    with measure_usage() as task:
        with measure_usage() as step:
            record_usage("product/model", input_tokens=10, output_tokens=5, cost=0.002, latency_ms=40.0)
        record_usage("product/model", input_tokens=3, output_tokens=1)  # no cost surfaced: unknown, not free
    assert (step.calls, step.input_tokens, step.output_tokens, step.cost_usd) == (1, 10, 5, 0.002)
    assert (task.calls, task.input_tokens, task.calls_without_cost) == (2, 13, 1)
    assert task.cost_usd == 0.002 and task.by_model["product/model"].calls == 2


def test_recording_outside_a_scope_is_a_no_op():
    record_usage("product/model", input_tokens=1)  # must not raise
    with measure_usage() as later:
        pass
    assert later.calls == 0


def test_tracing_is_a_no_op_without_langfuse(monkeypatch):
    for var in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("RAG_TRACE_LEVEL", "generations")
    ran = []
    with traced_run(job_id="job-1", name="product-run", metadata={"tenant": "t1"}):
        with traced_step("product-step", metadata={"k": "v"}):
            ran.append(1)
    assert ran == [1]


def test_the_exports_are_the_engine_objects():
    from rag_wright import api
    from rag_wright.models import tracing, usage

    assert api.record_usage is usage.record_usage
    assert (api.traced_run, api.traced_step) == (tracing.traced_run, tracing.traced_step)
    assert {"record_usage", "traced_run", "traced_step"} <= set(api.__all__)
