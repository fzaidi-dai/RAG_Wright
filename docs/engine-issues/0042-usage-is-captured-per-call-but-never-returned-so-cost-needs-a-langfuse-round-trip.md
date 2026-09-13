# Engine issue 0042: token usage and real cost are captured per call but never returned, so the product must read them back out of Langfuse

**Raised by:** RuleWright (product) · **Date:** 2026-09-13 · **Severity:** medium — cost is measurable but
only indirectly, and the indirection is what keeps breaking
**Affects:** `models/seam.py` (captures `usage_metadata` / `response_metadata.token_usage`, including
OpenRouter's real per-call `cost`) · `models/tracing.py::generation` · every capability that invokes a model
**Not a tracing bug:** tracing works. This is about the usage never being available to the caller in-band.

---

## Summary

The engine already holds exactly the numbers a caller needs, at the moment of each model call.
`models/seam.py:320-323` reads `usage_metadata`, and `response_metadata["token_usage"]["cost"]` —
**OpenRouter's own per-call cost, not a price-table estimate** (engine issue 0021). It sends them to
Langfuse as a generation and **returns none of it**: a capability's result is graph state, with no usage on
it.

So a product that wants "what did this operation cost" cannot ask the call that made the cost. It must:

1. choose a correlation id and open `traced_run` so the generations are grouped,
2. wait for Langfuse to accept and index them,
3. query Langfuse back, and
4. re-aggregate what the engine already had in hand.

**We would like the usage returned alongside the result**, so a caller can total it locally.

## Why this is worth changing, from the product side

**Every failure we have had measuring cost has been in the round trip, never in the numbers.** The engine's
figures have been right every time we could read them. What broke, repeatedly, was the reading:

- a **time-window read** that silently could not attribute, because the store held 10,140 traces in the
  window against a page limit of 100 (our T-3.21). The figures existed; we could not see them.
- **ingestion policy in the store**: `observations.get_many` and the metrics aggregate API both answer
  `404 — "only available in a Langfuse v4 write mode"` on our deployment, so the only available shape is
  list-traces-then-fetch-each.
- **flush-versus-arrival**: a read immediately after a run returns a SHORT COUNT that looks like a real one.
  We wait for the trace count to stop growing, which is a heuristic standing in for a fact.

None of that is the engine's fault, and all of it disappears if the number comes back with the answer.

**And attribution stops needing a correlation id at all.** Our product knows which customer and workspace a
call was made for; it does not know it *at Langfuse read time* without threading an id through and mapping
it back. In-band usage makes tenant attribution a local fact — which is what we need for billing, and what
we would rather not solve by putting customer identifiers into a telemetry store.

## What we are asking for

**Usage on the result, in whatever shape fits the engine's contracts.** Two possibilities, and we have no
preference between them — the engine owns this shape:

- a `usage` entry in the returned state of each capability/subgraph: `{calls, input_tokens, output_tokens,
  cost_usd, by_model}` accumulated for that invocation; or
- an accumulator the caller passes in, which the model seam adds to per call.

**What matters to us:**

1. **Round trips, not just tokens.** The count of model calls is the number people guess wrong — we
   measured query-side retrieval at two calls where one was assumed (our T-4.2), and 9 on a corpus query.
2. **OpenRouter's real cost where it exists**, with a clear signal when a model priced at zero because no
   price row existed, rather than a zero that reads as free.
3. **Per model**, since a single operation spans several.
4. **No behaviour change when tracing is off.** Usage is a property of the call; it should not require
   telemetry to be switched on. This is the part that most affects us: `RAG_TRACE_LEVEL` defaults to `off`,
   so today a cost-measured run and a normal run are different runs.

## What we are NOT asking for

- **Not a replacement for tracing.** Langfuse remains the right place for per-generation detail, prompts,
  latency and cross-run analysis. This is about the total coming back with the answer.
- **Not a new dependency or an exporter.** The values are already parsed; this is about surfacing them.

## How we would verify it

A live ingest and a live corpus query, asserting the returned usage matches what Langfuse records for the
same run — same call count, same tokens, same cost. We have both halves already: our `usage_since(...,
session_ids=...)` reads the Langfuse side exactly, so the two can be compared directly rather than trusted.

## Our current position

We are not blocked. Our session-scoped read is exact today (a corpus query reads back as 9 round trips,
17,557 tokens, $0.014270). We are asking because the indirection is fragile in a way the numbers are not,
and because it is the only thing standing between us and per-customer cost attribution that never leaves
our own database.
