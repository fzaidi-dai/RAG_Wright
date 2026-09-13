"""In-band model usage accounting (engine issue 0042): total round trips, tokens, OpenRouter's real per-call
cost, and latency, returned to the caller WITHOUT a Langfuse round-trip and WITHOUT tracing being on.

A caller opens a `usage_scope()` around an operation; the model seam records each call into the active scope.
The scope is ambient via a `ContextVar`, so no capability signature or graph-state changes. Usage is a property
of the CALL, captured whenever a scope is active — independent of `RAG_TRACE_LEVEL` (which defaults to `off`).

Design notes (issue 0042, per RuleWright):
- `calls_without_cost` is a DISTINCT counter (top-level AND per model): a call the backend/model returned no
  cost for is UNKNOWN, never folded into `cost_usd` as a $0 that reads as free. A fully-unpriced run therefore
  has `cost_usd == 0.0` with `calls_without_cost == calls` — a signal never to print the total as money.
- NESTING IS ADDITIVE: a call records into EVERY active scope on the stack, so a task-level outer scope totals
  everything while inner per-operation scopes attribute their own slice. (Both are tested.)
- THREAD-SAFE: the accumulator is shared across the asyncio tasks and worker threads a scope spans (the engine
  copies the context across its `to_thread` / dedicated-executor boundaries — ISSUE-0018), and every add is
  locked, so concurrent model calls (the ingest/query gather + executor fan-out) accumulate correctly.
"""

from __future__ import annotations

import contextvars
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator, Optional


@dataclass
class ModelUsage:
    """Per-model totals within a scope (`cost_usd` sums KNOWN per-call costs only)."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    calls_without_cost: int = 0  # calls the backend/model returned NO cost for -> unknown, NOT free
    latency_ms_total: float = 0.0  # sum of per-call wall-clock ms; avg = latency_ms_total / calls


@dataclass
class UsageTotals:
    """The usage accumulated within one `usage_scope()`: top-level totals + a per-model breakdown."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    calls_without_cost: int = 0
    latency_ms_total: float = 0.0
    by_model: dict[str, ModelUsage] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def _add(self, model: str, input_tokens: int, output_tokens: int,
             cost: Optional[float], latency_ms: Optional[float]) -> None:
        with self._lock:
            per = self.by_model.get(model)
            if per is None:
                per = ModelUsage()
                self.by_model[model] = per
            for tgt in (self, per):  # same shared counters on the top-level totals and this model's row
                tgt.calls += 1
                tgt.input_tokens += int(input_tokens or 0)
                tgt.output_tokens += int(output_tokens or 0)
                tgt.latency_ms_total += float(latency_ms or 0.0)
                if cost is None:
                    tgt.calls_without_cost += 1
                else:
                    tgt.cost_usd += float(cost)


# The STACK of active scopes (innermost last). A tuple set per scope-enter, so parallel contexts are isolated
# and copy_context() carries the whole stack (same UsageTotals objects) into a worker thread / executor.
_STACK: contextvars.ContextVar[tuple[UsageTotals, ...]] = contextvars.ContextVar("rag_usage_stack", default=())


@contextmanager
def usage_scope() -> Iterator[UsageTotals]:
    """Accumulate model usage for the operation run inside the block; returns the totals to read after it.

    Nesting is additive: a call made inside an inner scope is counted by the inner scope AND every enclosing
    scope, so a task-level outer scope totals everything while inner per-operation scopes attribute their slice."""
    totals = UsageTotals()
    token = _STACK.set(_STACK.get() + (totals,))
    try:
        yield totals
    finally:
        _STACK.reset(token)


def usage_capturing() -> bool:
    """True when at least one usage scope is active — the seam checks this to decide whether to capture usage
    (e.g. request `stream_usage`) on a call that would otherwise not need it."""
    return bool(_STACK.get())


def record_usage(model: str, *, input_tokens: int = 0, output_tokens: int = 0,
                 cost: Optional[float] = None, latency_ms: Optional[float] = None) -> None:
    """Record one model call into every active scope (a no-op when none is active, so it is safe to call on
    every model call regardless of tracing). `cost=None` means the backend surfaced no cost (counted as
    `calls_without_cost`, never as $0). `model` is the engine model-id string, keyed consistently across paths."""
    for totals in _STACK.get():
        totals._add(model, input_tokens, output_tokens, cost, latency_ms)
