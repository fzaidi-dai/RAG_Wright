# RuleWright handoff: in-band model usage (issue 0042) — cost/tokens/round-trips without a Langfuse round-trip

Date: 2026-09-13 · on `origin/main` · ADR-0105 · **No API change to any capability/graph. New `usage_scope()` you opt into.**

---

## What you get

Wrap any operation in `usage_scope()`; the seam records every model call into it, and you read the totals locally after — no correlation id, no Langfuse read, and **it works with tracing off** (`RAG_TRACE_LEVEL=off`, your default):

```python
from rag_wright.models.usage import usage_scope

with usage_scope() as u:
    result = await graph.ainvoke({"query": ...})     # or any capability call
# u.calls, u.input_tokens, u.output_tokens, u.cost_usd, u.calls_without_cost, u.latency_ms_total, u.by_model
# u.by_model["qwen/qwen3.8-27b"] -> ModelUsage(calls, input_tokens, output_tokens, cost_usd, calls_without_cost, latency_ms_total)
```

Your four asks, all in:
1. **Round trips** — `u.calls` (and per model). Counted on every path, including calls that return no usage metadata.
2. **Real cost + unpriced signal** — `u.cost_usd` sums OpenRouter's ACTUAL per-call cost; a call the backend surfaced no cost for increments **`calls_without_cost`** (top-level and per-model), never a misleading $0. A fully-unpriced run is `cost_usd == 0.0` with `calls_without_cost == calls` — do not print that as money; per-model tells you which model to add a price row for.
3. **Per model** — `u.by_model`, keyed by the engine model id.
4. **No tracing dependency** — usage is a property of the call; a cost-measured run and a normal run are the same run.

Plus your three shape asks: **`calls_without_cost` is inside `by_model`** too; **latency** is `latency_ms_total` (per-model and total; avg = `latency_ms_total / calls`); **nesting is additive** — a task-level outer scope totals everything while inner per-operation scopes attribute their slice.

## Completeness (the point of req#1)

Captured at all four call paths: `build_structured` (judges), `astream_text` (generation + the async tag-parse extraction), direct `build_model().invoke`, and the **litellm docling-graph path** (party/clause extraction). So ingest — the expensive path — comes back complete, not a confident undercount.

## Threads — the failure mode you flagged

A `ContextVar` isn't inherited by a bare worker thread. The engine already copies the context across its `to_thread` / dedicated-executor boundaries (ISSUE-0018), so the scope propagates to the executor-run extraction and the `to_thread` stages, and calls there land in the scope. Tested both ways: a `ThreadPoolExecutor` call recorded inside `copy_context().run(...)` lands in the scope; a bare worker without the copied context records into **no** scope (a no-op, never mis-attributed).

## Verify it your way

Run a live ingest and a live corpus query inside `usage_scope()` and compare `u` against your `usage_since(..., session_ids=...)` Langfuse read for the same run — same call count, same tokens, same cost. Engine-side live smoke (tracing off): a 2-call scope reported `calls=2, in=334, out=79, cost=$0.000279, unpriced=0` with a per-model row. If the two ever disagree, that's a bug we want — send us the run.

## The one internal change worth knowing (your condition, met)

`build_structured` now always carries `include_raw` internally to read usage off the raw response. It is **purely client-side** (no extra tokens, no extra round trip), and the caller's exact output shape and the raise-on-parse-failure contract are unchanged (tested at that boundary). Tracing behavior is unchanged and complementary — Langfuse remains the place for per-generation detail; this just returns the total with the answer.

Reference: ADR-0105, `models/usage.py` (`usage_scope`, `UsageTotals`, `record_usage`), `models/seam.py` (`build_structured`, `astream_text`), `capabilities/dg_extraction.py` (litellm path), engine issue `docs/engine-issues/0042-...`. Full suite: 1576 passed.
