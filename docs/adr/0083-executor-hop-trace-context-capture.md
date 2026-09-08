# ADR-0083: extraction offload carries the trace context across the executor hop

**Status:** accepted · **Date:** 2026-09-08 · **Issue:** engine 0018 (RuleWright) · **Extends:** ADR-0078 (dedicated extraction executor), ADR-0017-era tracing (`models/tracing.py`)

## Context

Issue 0017 gave the engine Langfuse tracing: `traced_run` groups every generation a document (or query) emits under one session, using langfuse's `propagate_attributes`, which sets **OTel ambient context** (stored in `contextvars`: `langfuse.propagated.session_id`).

ADR-0078 routes all docling-graph extraction — clause, party, claim, requirement — through `aextract_parties`, which offloads the synchronous `extract_parties` to a **dedicated `ThreadPoolExecutor`** via `loop.run_in_executor(...)`.

A `ThreadPoolExecutor` worker starts with an **empty** `contextvars` context. So the OTel context that `traced_run` established on the event-loop thread did **not** reach the extraction worker, and the docling-graph generation emitted there (`dg_extraction.party` / `dg_extraction.clause`, the `litellm` path) landed in its own root trace with `sessionId: null` — invisible to cost-per-document and cost-per-query. The `astream_text` generations, which stay on the loop, remained correlated. RuleWright measured this as 16.7% uncorrelated on a small NDA ingest and **100% uncorrelated on the query leg** (a query has no per-clause spend to amortise against — the extraction calls are the whole spend).

## Decision

In `aextract_parties`, capture the current context at submit time and run the worker inside it:

```python
ctx = contextvars.copy_context()
call = partial(extract_parties, text, model, **kwargs)
return await loop.run_in_executor(extraction_executor(), lambda: ctx.run(call))
```

- **Per-call** `copy_context()` (not a shared snapshot): each concurrent extraction carries its own document/query session, so concurrent users are not summed into each other's figures — the production-accounting requirement in issue 0018.
- Applied at the single funnel (`aextract_parties`), so it covers every extraction kind and the `litellm` emission point inside `extract_parties` uniformly.
- Transparent when tracing is off: `copy_context()`/`ctx.run` just carry whatever context exists (an empty OTel context), no behavior change and negligible cost.

## Consequences

- Every generation an extraction emits is attributed to its document/query session, whichever executor thread it runs on. The query leg goes from 100% uncorrelated to correlated; the ingestion floor becomes a total.
- Any *other* call site that crosses a thread/process boundary before emitting a generation would need the same treatment. Today the only one that reached Langfuse uncorrelated was the extraction offload; if a new boundary appears, apply the same capture.
- Verified by a hermetic test (a contextvar set before the call is visible on the worker thread) and a live gate (with a real langfuse OTel provider, the worker sees the identical context carrying `langfuse.propagated.session_id`).
