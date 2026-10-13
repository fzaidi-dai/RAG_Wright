"""The substrate's half of accounting a run: time it, trace it, and always tell the accounting (stage 4b).

**What this file tests after T-7.58 stage 4b, and what moved away.** Every assertion about a LEDGER -- the
row's kind, its tenancy columns, an unmeasured run writing nothing -- went to
`test_agent_run_accounting.py` with the code it tests. `accounted_run` no longer knows what a ledger is: it
measures wall-clock duration, groups the run's generations under its run id, and calls the accounting the
CALLER supplied.

**So the fake here has no ledger in it at all**, which is the point of the stage. If `accounted_run` ever
needs more than `collecting()`, `record(...)` and `trace_metadata` from its caller, this fake stops
compiling and the seam has leaked.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from rag_wright.harness.accounting import accounted_run


def settings() -> SimpleNamespace:
    """The caller's settings handle: the substrate reads no field of it (tracing is the engine's configuration)."""
    return SimpleNamespace()


class Accounting:
    """Everything the substrate is allowed to need, and nothing else."""

    def __init__(self) -> None:
        self.recorded: list[dict] = []
        self.collected = 0
        self.trace_metadata = {"scope": "whatever-the-product-says"}

    @asynccontextmanager
    async def _collect(self):
        self.collected += 1
        yield

    def collecting(self):
        return self._collect()

    async def record(self, *, run_id: str, since, duration_ms: float) -> None:
        self.recorded.append({"run_id": run_id, "since": since, "duration_ms": duration_ms})


@pytest.mark.asyncio
async def test_THE_RUN_IS_TIMED_AND_THE_DURATION_IS_HANDED_OVER() -> None:
    """**Wall-clock, and the substrate is the only thing that can measure it.** It differs from summed
    model latency whenever tools run concurrently or a human is waited on, and it is what makes a
    per-second-billed deployment apportionable later. The accounting cannot compute it: by the time
    `record` is called the run is over."""
    accounting = Accounting()

    async with accounted_run(settings(), run_id="run-1", accounting=accounting):
        pass

    [row] = accounting.recorded
    assert row["run_id"] == "run-1", "the run id is what joins the spend back to the run"
    assert row["duration_ms"] is not None and row["duration_ms"] >= 0.0
    assert row["since"] is not None, "the accounting needs a window to read its spend back over"


@pytest.mark.asyncio
async def test_THE_CALLERS_COLLECTION_IS_OPENED_AROUND_THE_RUN() -> None:
    """A tool's engine work is collected through the caller's own context manager, for the whole run."""
    accounting = Accounting()

    async with accounted_run(settings(), run_id="run-1", accounting=accounting):
        pass

    assert accounting.collected == 1, "the caller's collection was never opened"


@pytest.mark.asyncio
async def test_NO_ACCOUNTING_IS_A_NO_OP() -> None:
    """A test or a script runs unchanged -- accounting must never be why work does not happen. The run is
    still timed and traced; nothing is recorded, because there is nothing to record into."""
    ran = []

    async with accounted_run(settings(), run_id="run-1", accounting=None):
        ran.append(True)

    assert ran == [True]


@pytest.mark.asyncio
async def test_THE_ACCOUNTING_IS_TOLD_EVEN_WHEN_THE_WORK_RAISES() -> None:
    """A failed run still spent money. Recording only successes under-reports exactly the runs somebody is
    most likely to ask about -- so `record` is awaited in a `finally`, which is the substrate's half of
    "a ledger failure never fails the run"."""
    accounting = Accounting()

    with pytest.raises(ValueError):
        async with accounted_run(settings(), run_id="run-1", accounting=accounting):
            raise ValueError("the model refused")

    assert len(accounting.recorded) == 1, "a run that failed mid-answer went unaccounted"


@pytest.mark.asyncio
async def test_A_BROKEN_ACCOUNTING_IS_THE_CALLERS_PROBLEM_NOT_THE_RUNS() -> None:
    """**Where the rule now lives, stated rather than assumed.** `AgentRunAccounting` swallows its own
    ledger failures (`test_agent_run_accounting.py` plants both halves). The substrate does NOT add a
    second net: an accounting that raises is a programming error in the caller, and hiding it here would
    mean a product could lose every row and see nothing. This test pins the choice so it is deliberate."""
    class Broken(Accounting):
        async def record(self, **_kwargs) -> None:
            raise RuntimeError("the caller's accounting is broken")

    with pytest.raises(RuntimeError):
        async with accounted_run(settings(), run_id="run-1", accounting=Broken()):
            pass
