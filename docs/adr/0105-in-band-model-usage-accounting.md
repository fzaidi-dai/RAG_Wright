# ADR-0105: in-band model usage accounting (usage returned with the answer, no Langfuse round-trip)

**Status:** accepted · **Date:** 2026-09-13 · **Resolves:** engine issue 0042 · **Related:** ADR-0100 (model seam), ISSUE-0025 (structured-call tracing), issue 0021 (OpenRouter real cost), ISSUE-0018 (context copied across the extraction executor)

## Context

The seam already parses token usage and OpenRouter's real per-call `cost` at each model call, but only inside the `traced` branch, and only emits them to Langfuse — nothing comes back to the caller. So a product measuring "what did this operation cost" had to choose a correlation id, wait for Langfuse to index, read it back, and re-aggregate — an indirection that broke repeatedly (page limits, ingestion-mode 404s, flush-vs-arrival short counts) while the numbers themselves were always right. RuleWright asked for the usage returned alongside the result, so a caller totals it locally (and attributes per-customer without putting customer ids in a telemetry store). Requirements: complete round-trip counts, OpenRouter's real cost with a clear "unpriced" signal, per-model, and **no dependence on tracing** (`RAG_TRACE_LEVEL` defaults to `off`).

## Decision

**An ambient `usage_scope()` (a `ContextVar`), recorded into by the seam on every call, independent of tracing.**

- `models/usage.py`: `usage_scope()` yields a `UsageTotals` (`calls, input_tokens, output_tokens, cost_usd, calls_without_cost, latency_ms_total, by_model`). `record_usage(model, …)` adds one call into every active scope; a no-op when none is active, so it is safe to call on every model call. No capability signature or graph-state changes (chosen over threading `usage` through returned state — it would churn every capability and miss paths).
- **`calls_without_cost` is a distinct counter, per-model and top-level.** A call the backend surfaced no cost for is UNKNOWN, never folded into `cost_usd` as a $0 that reads as free. A fully-unpriced run is `cost_usd == 0.0` with `calls_without_cost == calls` — the signal never to print the total as money; per-model it names which model needs a price row.
- **Latency** (`latency_ms_total`, per-model and total; avg = total/calls) — so a caller renders avg latency per call without Langfuse.
- **Nesting is additive**: a call records into every scope on the stack, so a task-level outer scope totals everything while inner per-operation scopes attribute their slice (both tested).
- **Capture at all four call paths, regardless of tracing** (gated on `traced OR scope-active`): `build_structured` (forces `include_raw` internally — client-side only, no extra tokens/round trip — and reads usage off the raw at finish, then restores the caller's exact shape + raise-on-parse contract; the runnable is built before the scope is entered, so this is the one hot-path change, functionally equivalent per ISSUE-0025), `astream_text` (per-call: `stream_usage` when capturing + the cost-capturing client), direct `build_model().invoke` (via the same client), and the **litellm docling-graph extraction path** (dg_extraction) so ingest round trips are complete.
- **Threads**: the accumulator rides the same `contextvars.copy_context()` / `asyncio.to_thread` propagation the engine already uses across its executor boundaries (ISSUE-0018). A worker that does NOT carry the context records into no scope (a no-op, never mis-attributed) — tested both directions, including a `ThreadPoolExecutor` call landing in the scope via `copy_context().run`.

## Consequences

- A caller totals cost/tokens/round-trips locally: `with usage_scope() as u: graph.invoke(...)` — no correlation id, no Langfuse read, no telemetry dependency, works with tracing off. Live-verified against OpenRouter (tracing off): a 2-call scope reported `calls=2, in=334, out=79, cost=$0.000279, unpriced=0` with a per-model row.
- Tracing is unchanged and complementary (per-generation detail, prompts, cross-run analysis stay in Langfuse). This is not a replacement or a new dependency — the values were already parsed.
- The one broad change is `build_structured` always carrying `include_raw` internally; the external shape and parse-error contract are identical (tested at that boundary) and there is no request-side cost.

Full suite: 1576 passed, 44 skipped.
