"""Shared fakes for the async engine tests (ASYNC-* arc, ADR-0057). Hermetic -- no network; the only event
loop is the one the running async test provides.
"""
from __future__ import annotations

import asyncio
from typing import Any


class FakeAsyncRunnable:
    """A stand-in for an async model runnable: an awaitable `.ainvoke`, configurable to succeed, raise, or stall.

    - `result`   : value returned on success.
    - `raises`   : an exception instance raised instead of returning.
    - `stall_s`  : if set, `await asyncio.sleep(stall_s)` before returning/raising -- to exercise the
                   `asyncio.timeout` deadline (a stalled call must be *cancelled*, not merely abandoned).
    - `fail_times`: raise `raises` (or a ValueError) on the first N calls, then succeed -- to exercise the
                   bounded transient retry.
    """

    def __init__(self, *, result: Any = "ok", raises: BaseException | None = None,
                 stall_s: float | None = None, fail_times: int = 0) -> None:
        self._result = result
        self._raises = raises
        self._stall_s = stall_s
        self._fail_times = fail_times
        self.calls = 0

    async def ainvoke(self, x: Any = None) -> Any:
        self.calls += 1
        if self._stall_s is not None:
            await asyncio.sleep(self._stall_s)
        if self.calls <= self._fail_times:
            raise (self._raises or ValueError("simulated transient"))
        if self._raises is not None and self._fail_times == 0:
            raise self._raises
        return self._result
