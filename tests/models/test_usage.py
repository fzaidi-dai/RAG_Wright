"""Issue 0042: in-band model usage accounting. Hermetic -- exercises the accumulator, nesting, and the
thread/executor propagation that a ContextVar makes easy to get silently wrong."""

from __future__ import annotations

import concurrent.futures
import contextvars

from rag_wright.models.usage import record_usage, usage_capturing, usage_scope


def test_records_calls_tokens_cost_latency_per_model_and_total():
    with usage_scope() as u:
        record_usage("qwen/x", input_tokens=100, output_tokens=20, cost=0.001, latency_ms=50.0)
        record_usage("qwen/x", input_tokens=200, output_tokens=30, cost=0.002, latency_ms=70.0)
        record_usage("granite/y", input_tokens=10, output_tokens=5, cost=0.0005, latency_ms=30.0)
    assert u.calls == 3 and u.input_tokens == 310 and u.output_tokens == 55
    assert round(u.cost_usd, 6) == 0.0035 and round(u.latency_ms_total, 1) == 150.0
    assert u.by_model["qwen/x"].calls == 2 and u.by_model["qwen/x"].input_tokens == 300
    assert round(u.by_model["qwen/x"].cost_usd, 6) == 0.003
    assert u.by_model["granite/y"].calls == 1


def test_unpriced_call_is_counted_distinctly_never_as_zero_money():
    with usage_scope() as u:
        record_usage("m/priced", input_tokens=10, output_tokens=5, cost=0.001)
        record_usage("m/unpriced", input_tokens=10, output_tokens=5, cost=None)  # no price row -> unknown
    assert u.calls == 2 and u.calls_without_cost == 1        # top-level signal: 1 call unpriced
    assert round(u.cost_usd, 6) == 0.001                     # the unpriced call adds nothing (not a $0)
    assert u.by_model["m/unpriced"].calls_without_cost == 1  # per-model: which model needs a price row
    assert u.by_model["m/priced"].calls_without_cost == 0


def test_fully_unpriced_run_is_zero_cost_with_all_calls_flagged():
    # the signal RuleWright relies on: cost_usd == 0.0 AND calls_without_cost == calls -> never print as money.
    with usage_scope() as u:
        record_usage("m", cost=None)
        record_usage("m", cost=None)
    assert u.cost_usd == 0.0 and u.calls_without_cost == u.calls == 2


def test_nesting_is_additive_outer_totals_everything_inner_attributes_its_slice():
    with usage_scope() as outer:
        record_usage("m", input_tokens=1, output_tokens=1, cost=0.001)  # outer-only
        with usage_scope() as inner:
            record_usage("m", input_tokens=2, output_tokens=2, cost=0.002)  # counted by BOTH
        record_usage("m", input_tokens=4, output_tokens=4, cost=0.004)  # outer-only again (inner closed)
    assert inner.calls == 1 and round(inner.cost_usd, 6) == 0.002        # inner: just its slice
    assert outer.calls == 3 and round(outer.cost_usd, 6) == 0.007        # outer: everything


def test_record_outside_any_scope_is_a_noop():
    assert usage_capturing() is False
    record_usage("m", input_tokens=1, output_tokens=1, cost=0.001)       # must not raise, nothing to record


def test_usage_capturing_reflects_active_scope():
    assert usage_capturing() is False
    with usage_scope():
        assert usage_capturing() is True
    assert usage_capturing() is False


def test_a_call_on_a_worker_thread_lands_in_the_scope_when_context_is_copied():
    # THE failure mode RuleWright flagged: a ContextVar is NOT inherited by a bare executor worker, so a model
    # call on a thread would be silently uncounted. The engine copies the context across its executor/to_thread
    # boundaries (ISSUE-0018); this asserts that a call recorded inside `copy_context().run(...)` on a
    # ThreadPoolExecutor still lands in the enclosing scope.
    with usage_scope() as u:
        ctx = contextvars.copy_context()

        def _on_worker():
            record_usage("m/worker", input_tokens=7, output_tokens=3, cost=0.0009, latency_ms=12.0)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            ex.submit(lambda: ctx.run(_on_worker)).result()
    assert u.calls == 1 and u.by_model["m/worker"].input_tokens == 7   # recorded from the worker thread
    assert round(u.cost_usd, 4) == 0.0009


def test_a_bare_worker_without_copied_context_does_not_leak_across_scopes():
    # the negative: a worker that does NOT carry the context sees no scope -> its record is a silent no-op (it does
    # not land in the wrong scope). This is why the engine must copy the context; documented so the contract is clear.
    with usage_scope() as u:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            ex.submit(record_usage, "m/bare", input_tokens=99, output_tokens=99, cost=1.0).result()
    assert u.calls == 0   # not carried -> not counted (never mis-attributed)
