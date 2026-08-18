"""ASYNC-A1 (ADR-0057): the async test infrastructure works. Proves an `async def test_*` runs natively under
`asyncio_mode = "auto"`, and that `asyncio.timeout` truly CANCELS a stalled coroutine -- the primitive the
ASYNC-A2 model-call deadline is built on, and the semantic difference from a thread watchdog (a real cancel
delivered into the call, i.e. socket teardown, not merely walking away). Hermetic.
"""
from __future__ import annotations

import asyncio

import pytest

from tests.async_helpers import FakeAsyncRunnable


async def test_async_test_runs_under_auto_mode():
    r = FakeAsyncRunnable(result="ok")
    assert await r.ainvoke("x") == "ok"
    assert r.calls == 1


async def test_asyncio_timeout_raises_on_a_stalled_call():
    # a call that outlasts the deadline is bounded by the timeout, not by the call itself.
    r = FakeAsyncRunnable(stall_s=5.0)
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):
            await r.ainvoke("x")


async def test_timeout_delivers_cancellation_into_the_call():
    # the load-bearing property: CancelledError is delivered INTO the coroutine (true cancel / socket teardown),
    # not just "we stopped awaiting". This is what a synchronous thread watchdog cannot do.
    cancelled = {"hit": False}

    async def _slow():
        try:
            await asyncio.sleep(5.0)
        except asyncio.CancelledError:
            cancelled["hit"] = True
            raise

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):
            await _slow()
    assert cancelled["hit"]


async def test_fake_runnable_retry_and_raise_shapes():
    # the helper the retry/deadline tests (A2+) will reuse: fail-then-succeed and always-raise.
    flaky = FakeAsyncRunnable(fail_times=2, result="done")
    with pytest.raises(ValueError):
        await flaky.ainvoke()
    with pytest.raises(ValueError):
        await flaky.ainvoke()
    assert await flaky.ainvoke() == "done" and flaky.calls == 3

    boom = FakeAsyncRunnable(raises=KeyError("nontransient"))
    with pytest.raises(KeyError):
        await boom.ainvoke()
