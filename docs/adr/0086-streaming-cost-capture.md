# ADR-0086: recover OpenRouter's real cost from the streaming path

**Status:** accepted · **Date:** 2026-09-08 · **Issue:** engine 0021 (RuleWright) · **Builds on:** 0017-era tracing, ADR-0057 (async deadline), ADR-0085 (query leg moved onto `astream_text`)

## Context

Issue 0017's emission contract said cost is a pass-through of OpenRouter's actual reported cost, and where the SDK does not surface it (LangChain streaming) `cost` is None and Langfuse prices from its own model table. After ADR-0085 moved query constraint extraction onto the tag-parse / free-text path, `query-constraints` runs through `astream_text`, so its generations reported `cost=None` — and with no Langfuse price row for the newly adopted `qwen/qwen3.8-27b`, **2,335 real tokens showed as $0.00 / UNPRICED**.

The premise ("streaming has no cost") is false. Grounded against the installed `langchain_openai 1.3.3`:
- OpenRouter returns `cost` (and `cost_details`) on every call, and it is on the wire during streaming too (the final chunk's `usage` when `stream_options.include_usage`).
- `ainvoke` preserves the raw `token_usage` (incl. `cost`) in `response_metadata`; the **streaming** path does not — `_create_usage_metadata` builds `UsageMetadata` from a hardcoded whitelist and never reads `cost`, and the streaming path keeps no copy of the raw usage.

So the loss is in LangChain's streaming normalization, above the socket — the value already arrives.

## Decision

A thin subclass, `_CostCapturingChatOpenAI`, overrides the (overridable) per-chunk converter `_convert_chunk_to_generation_chunk`, which receives the raw chunk dict (with `usage.cost`), to capture the cost into a per-instance `_cost_holder` before delegating to `super()`. `build_model` gains an optional `_client_cls` (default unchanged), and `astream_text` builds the cost-capturing client and passes the captured cost to `record_generation` — the same pass-through the litellm path already does.

This is issue 0021's option 1 ("read the raw chunk / a thin subclass"), chosen over:
- **Option 3 (switch to `ainvoke`)** — `ainvoke` surfaces cost, but drops streaming's idle-drip / incremental-cancellation (ADR-0057). The issue itself flagged this as a real property loss to reject, and the no-regression rule forbids trading robustness for cost.
- **Option 4 (fetch after the fact)** — a per-call extra round trip.

The subclass keeps ALL streaming machinery (idle-drip `stream_chunk_timeout`, the total `asyncio.timeout` deadline, bounded retries, content accumulation) untouched — it only taps the raw chunk.

## Consequences

- Both emission paths now pass the provider's ACTUAL cost: the litellm path (`response.usage.cost`) and the `astream_text` path (raw chunk). Adopting a new model no longer needs a hand-maintained Langfuse price row for streaming generations. Live-verified: `query-constraints` emits `cost≈0.00116` where it previously emitted None.
- `cost` is None only when the backend genuinely does not surface it (e.g. self-hosted vLLM), and Langfuse then prices from its table.
- The cost read is `getattr`-guarded so a substituted client (tests, or a non-cost-capturing class) degrades to no cost rather than raising. `build_model` resolves the client class at call time so a monkeypatched `seam.ChatOpenAI` (tests) is still honored.
