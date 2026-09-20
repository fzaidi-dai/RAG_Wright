# ADR-0113: real span durations, TTFT, retrieval spans, and provider generation ids (issue 0048, part 1)

**Status:** accepted · **Date:** 2026-09-20 · **Addresses:** engine issue 0048 (the instrumentation half) · **Related:** ADR/issue 0017 (Langfuse emission), issue 0042 (in-band usage), ADR-0111 (the `deepinfra/bf16` pin under measurement)

## Context

Issue 0048: three live decomposition runs died on timeouts, and the trace couldn't tell a prompt change from provider noise — because the engine's Langfuse instrumentation was blind in three ways:

1. **Zero-duration generations.** `record_generation` ran *after* the model call and did `start_observation()` immediately followed by `end()`, so `start_time ≈ end_time` and **Langfuse's own latency column read 0** — the real figure survived only in `metadata.latency_ms`. langfuse v4 has no API to back-date a span's start (`start_observation` stamps "now"; only `end(end_time=)` is settable), so a post-hoc emit *cannot* carry a correct duration.
2. **Retrieval invisible.** The engine's retrieval (ArcadeDB + rehydration) is wrapped in `subgraphs.observability.business_span`, an **OTel-ambient** span that no-ops under a Langfuse-only setup — so `ask_contract_question`'s pre-generation cost never appeared in Langfuse; only the generation did.
3. **Slow calls unattributable.** OpenRouter returns a generation id resolvable at `/api/v1/generation?id=` (queue vs generation time), but the engine didn't record it, so a 257s tail couldn't be attributed to queueing vs long output.

## Decision

**Instrument the engine's model calls so latency is real and attributable.** (The endpoint/pin question — whether `allow_fallbacks:false` on ADR-0111 worsens the tail — is the *other* half of 0048, measured separately with this instrumentation; not decided here.)

1. **Open the generation BEFORE the call, end it AFTER.** `tracing.record_generation` is replaced by a pair — `start_generation(...)` (opens the observation at the call start, returns it or None) and `finish_generation(gen, ...)` (attaches output/usage/cost and ends it). The span's own start/end are now the real wall-clock, so Langfuse's latency column is correct. Migrated at all three emitters: `build_structured` (structured — span-relevance, query-constraint, judge), `astream_text` (answer generation), and the docling-graph litellm path (clause/claim/party extraction). Error paths end the span too (no dangling spans). `record_generation` is kept as a thin post-hoc convenience but is no longer used internally.
2. **Time-to-first-token on the streaming path.** `astream_text` captures the wall-clock of the first content token and passes it as Langfuse `completion_start_time`, so queue+prefill is split from decode.
3. **Retrieval as its own Langfuse span.** A new `tracing.traced_step(name)` opens a Langfuse `span` observation (distinct from the OTel `business_span`); `intra_document_qa`'s `serve` and `assemble` nodes are wrapped, so retrieval is a sibling span of the generation in the same trace — the retrieval-vs-generation split visible at last.
4. **Provider generation id in metadata.** `_provider_gen_id(raw)` reads LangChain's `.id` (else `response_metadata.id`); all three emitters put it in the generation metadata as `openrouter_generation_id` when present (None on vLLM), so a slow call resolves at `/api/v1/generation`.

## Consequences

- Langfuse now shows the true duration of every engine model call, a separate retrieval span, TTFT, and (on OpenRouter) an id that attributes a slow call to queue vs generation. This is what makes 0048's latency measurable from the engine side — and it is the *only* latency source on self-hosted Modal (vLLM has no generation-id endpoint), so it is a prerequisite for the planned OpenRouter→Modal endpoint comparison, not just the OpenRouter path.
- `business_span` (OTel-ambient, GraphWright runtime) and `traced_step` (Langfuse) are deliberately separate seams; the retrieval nodes now carry both, each active in its own runtime.
- Fully gated and safe: every langfuse touch degrades to a no-op off tracing or on any error (tracing must never break a model call). Verified against the real langfuse SDK (start/finish/traced_step raise nothing; `completion_start_time`, span `as_type`, and the merged metadata are accepted).
- **Not covered:** the endpoint/pin latency verdict (0048 part 2), and the *product's* own `agent-llm` zero-duration defect (`harness/tracing.py`, product-owned, RuleWright is fixing it). The definitive "Langfuse latency column is non-zero" confirmation lands in the joint reproduction (the product's eval from its checkout against the seeded DB, engine instrumentation up).

Tests: `tests/models/test_tracing.py` (open-before/close-after; TTFT; cost; generation-id-ready), `tests/models/test_seam_stream.py` (astream opens-before, cost on finish). Full suite: 1586 passed, 44 skipped.
