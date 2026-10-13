"""Every agent run is timed, traced and handed to whoever accounts for it (T-5a.4a-iii, ANFR-3, NFR-3).

**What this module is after T-7.58 stage 4b, and what it deliberately no longer knows.** It times a run,
groups the run's generations under its run id, and tells the caller's accounting when the run ended. It no
longer knows what a ledger is: `recording_usage`, `usage_since`, the row's field names (`calls`,
`input_tokens`, `cost_usd`, `unpriced`, `by_model`), the ledger's word for an agent run, and our
`(customer_id, workspace_id)` pair all moved to `rulewright.operations.usage.AgentRunAccounting`.

**That coupling is the one the engine's review of ADR-0139 caught**, and it is the subtle kind: this module
named no domain concept and would have passed both of our earlier measurements, while carrying our tenancy
shape and our ledger schema into a substrate another product is meant to reuse. A second product accounts
differently -- different ledger, different tenancy key, perhaps no ledger at all -- and nothing here should
have to change for that.

**The loop this still closes.** `traced_agent_run` groups a run's generations under its run id; the
caller's `record` reads them back by that id and writes whatever a row means to it. That is the same
emit / read-back / record path the engine's work takes, and the reason it is the same path is that a second
metering mechanism would mean two answers to "what did this matter cost".

**`accounting=None` is a no-op, exactly as `usage=None` was.** A test or a script runs unchanged rather
than failing, because accounting must never be the reason work does not happen. The run is still timed and
traced; nothing is recorded.

**A ledger failure never fails the run.** That rule now lives with the ledger, in `AgentRunAccounting`,
which is where it belongs -- but the substrate holds the other half of it: `record` is awaited in a
`finally`, so a run that raises is still accounted.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, nullcontext
from datetime import UTC, datetime
from typing import Any

from rag_wright.harness.tracing import traced_agent_run

logger = logging.getLogger(__name__)


@asynccontextmanager
async def accounted_run(
    settings: Any,
    *,
    run_id: str,
    accounting: Any = None,
    name: str = "agent-run",
) -> AsyncIterator[None]:
    """Time and trace one agent run, and hand it to `accounting` when it ends.

    `accounting` is anything with `collecting()`, `record(run_id=, since=, duration_ms=)` and
    `trace_metadata` -- the product supplies it (`operations.usage.AgentRunAccounting`). `None` disables
    recording without disabling the run.

    **`collecting()` is opened OUTSIDE the trace and the timer is started before both**, which preserves
    the original order: the engine work a tool causes is collected through the caller's ContextVar for the
    whole run, including the setup the trace does not cover.

    **Opened here rather than on the route**, because a streamed response produces its body after the
    endpoint returns: a request-scoped dependency would be resting the ledger on when Starlette closes its
    exit stack. The door already owns the context and the trace; it owns this too, and every caller
    inherits it.
    """
    started = time.monotonic()
    since = datetime.now(UTC)

    # `nullcontext` has supported `async with` since 3.10, so the no-accounting path needs no second shape.
    collecting = accounting.collecting() if accounting is not None else nullcontext()
    metadata = dict(accounting.trace_metadata) if accounting is not None else {}

    async with collecting:
        with traced_agent_run(settings, session_id=run_id, name=name, metadata=metadata):
            try:
                yield
            finally:
                duration_ms = (time.monotonic() - started) * 1000.0
                if accounting is not None:
                    await accounting.record(run_id=run_id, since=since, duration_ms=duration_ms)
