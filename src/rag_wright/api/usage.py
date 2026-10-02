"""EP-API-5 (ADR-0117, ADR-0105): usage/cost on the engine API surface.

A product measures the model usage an engine call incurs -- calls, input/output tokens, known cost (plus the count
of calls the backend surfaced NO cost for), total latency, and a per-model breakdown -- by wrapping the call in
`measure_usage()`. It is the public face of the engine's in-band usage accounting (`models/usage.usage_scope`), so
the product reads stats without reaching into engine internals. Ambient (a `ContextVar`), additive across nesting
(an outer scope totals everything; inner scopes attribute their slice), and carried into worker threads/executors.

    with engine.measure_usage() as usage:
        out = await engine.ainvoke_subgraph("intra_document_qa", {...}, resources=ws)
    usage.calls, usage.input_tokens, usage.output_tokens, usage.cost_usd, usage.calls_without_cost,
    usage.latency_ms_total, usage.by_model  # {model_id: ModelUsage(...)}
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from rag_wright.models.usage import ModelUsage, UsageTotals, usage_scope

__all__ = ["measure_usage", "UsageTotals", "ModelUsage"]


@contextmanager
def measure_usage() -> Iterator[UsageTotals]:
    """Accumulate the model usage of every engine call made inside the block; read the returned `UsageTotals`
    after it. Nesting is additive, so a task-level scope totals everything while an inner per-call scope attributes
    its slice. Capturing is opt-in: with no active scope, the engine records usage nowhere (zero overhead)."""
    with usage_scope() as totals:
        yield totals
