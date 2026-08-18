"""ASYNC-A2 (ADR-0057): the async seam's bounded retry + TRUE total wall-clock deadline. Hermetic -- exercises
`_ainvoke_bounded` with a fake async runnable, no network. This is the piece that actually closes engine issue
0003: a slow-drip / connection-alive stall a per-socket-op timeout never catches is cancelled at the deadline
and surfaces as a terminal `ModelCallTimeout`.
"""
from __future__ import annotations

import pytest

from rag_wright.models import seam
from rag_wright.models.seam import ModelCallTimeout, _ainvoke_bounded
from tests.async_helpers import FakeAsyncRunnable


async def test_stalled_call_is_cancelled_at_the_deadline_as_terminal_timeout(monkeypatch):
    monkeypatch.setattr(seam, "_MODEL_DEADLINE_S", 0.05)
    r = FakeAsyncRunnable(stall_s=5.0)  # a call that outlasts the deadline (slow-drip / wedged peer)
    with pytest.raises(ModelCallTimeout):
        await _ainvoke_bounded(r, "x", "test-model")
    assert r.calls == 1  # cancelled mid-first-attempt, NOT retried -- a stall is terminal, not transient


def test_model_call_timeout_is_terminal_not_a_transient():
    # it must never be retried by the bounded loop nor (later) the pregel retry_on
    assert ModelCallTimeout not in seam._STRUCTURED_RETRY_ON
    assert not issubclass(ModelCallTimeout, seam._STRUCTURED_RETRY_ON)


async def test_transient_is_retried_then_succeeds(monkeypatch):
    monkeypatch.setattr(seam, "_backoff_s", lambda _a: 0.0)  # no real sleeps in the test
    r = FakeAsyncRunnable(fail_times=2, result="done")  # ValueError twice, then ok
    assert await _ainvoke_bounded(r, "x", "test-model") == "done"
    assert r.calls == 3  # bounded to the attempt budget


async def test_non_transient_is_not_retried():
    r = FakeAsyncRunnable(raises=KeyError("programming error"))
    with pytest.raises(KeyError):
        await _ainvoke_bounded(r, "x", "test-model")
    assert r.calls == 1  # KeyError is not in the transient set -> surfaced immediately


async def test_each_failed_attempt_is_logged(monkeypatch, caplog):
    monkeypatch.setattr(seam, "_backoff_s", lambda _a: 0.0)
    monkeypatch.setattr(seam, "_STRUCTURED_RETRY_ATTEMPTS", 2)
    r = FakeAsyncRunnable(raises=ValueError("boom"))
    with caplog.at_level("WARNING"):
        with pytest.raises(ValueError):
            await _ainvoke_bounded(r, "x", "modelX")
    assert any("modelX" in rec.getMessage() and "failed" in rec.getMessage() for rec in caplog.records)


async def test_retry_sleeps_count_against_the_same_total_deadline(monkeypatch):
    # the total is bounded however it is spent: a fast-but-always-failing call whose backoff sleeps exceed the
    # deadline is cancelled as a ModelCallTimeout, not allowed to run the full attempt budget.
    monkeypatch.setattr(seam, "_MODEL_DEADLINE_S", 0.05)
    monkeypatch.setattr(seam, "_backoff_s", lambda _a: 1.0)  # each backoff alone outlasts the deadline
    r = FakeAsyncRunnable(raises=ValueError("transient"))
    with pytest.raises(ModelCallTimeout):
        await _ainvoke_bounded(r, "x", "test-model")
