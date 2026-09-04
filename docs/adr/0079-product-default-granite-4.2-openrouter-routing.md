# ADR-0079: Product default moves granite-4.1-8b → granite-4.2-8b, with reasoning-off + OpenRouter provider routing

Date: 2026-09-04
Status: Accepted (implemented; MODEL-DEFAULT-1)

## Context

The product default LLM `ibm-granite/granite-4.1-8b` (ADR-0039 substrate) was **de-listed on OpenRouter** — every
call now returns HTTP 404 "No endpoints found". The default has to move to a listed model. This is an ask-first
model switch (CLAUDE.md); the candidates were evaluated first-hand, live, on the real ingestion path — not chosen
on catalog specs.

Two candidates were tested end-to-end (isolated calls AND the full docling-graph ingestion path, on a real
executed contract):

- **`~deepseek/deepseek-v4-flash-latest`** (pre-approved "if it works"): fast and correct on isolated calls, but
  **flaky on our docling-graph extraction path** — a single clean clause, retried 5×, failed 2/5–5/5 depending on
  provider (Parasail 3/5, DeepInfra 0/5, Fireworks 0/5), and the `~…-latest` + `sort:latency` alias is
  provider-roulette (a different underlying provider per call). On a real NDA it dead-lettered with 31/48 clause
  failures. **Not viable.**
- **`ibm-granite/granite-4.2-8b`** (the in-family successor): **5/5** reliable on isolated clause extraction, and
  on a real executed NDA it ingested **without dead-letter** and wrote a real KG
  (`{clauses, entities:2, edges:1, spans:7}` — both parties resolved + the CONTRACTS_WITH edge). Chosen.

Two model-behaviour facts, established empirically and now encoded in the profile (never hardcoded in node code):

1. **granite-4.2 is a reasoning model** — on a forced structured call it returns empty `content` unless reasoning
   is disabled. Reasoning-**on** is strictly worse for extraction: measured on the same real NDA it dead-lettered
   (party extraction empty), wrote 0 clauses, and took **177s vs 25s**. So the extraction path disables reasoning.
2. The `~…-latest` alias / OpenRouter provider pool routes non-deterministically; `provider:{sort:latency}` picks
   the fastest available provider, and an explicit `provider:{order:[…], allow_fallbacks:false}` pin is available
   for determinism.

## Decision

- `_PRODUCT_LLM` (every non-vision role) → `ibm-granite/granite-4.2-8b`; the extraction defaults and the
  MCP/compliance call sites move with it.
- Its model profile carries `extra_body = {"provider": {"sort": "latency"}, "reasoning": {"enabled": false}}`
  — provider routing + reasoning-off, applied at the single point a model is built (the seam), not in node code.
- The docling-graph litellm path (which builds its own request, so the seam profile does not reach it) injects the
  same routing in `_DeadlineBoundedLiteLLMClient._call_api`, gated on an OpenRouter base_url:
  - provider: `OPENROUTER_PROVIDER_ORDER` (comma-separated, no fallbacks) for a deterministic pin, else
    `OPENROUTER_SORT` (default `latency`);
  - reasoning: disabled by default, with an `RAG_EXTRACT_REASONING=1` A/B toggle kept as a deliberate lever.

## Consequences

- **A working OpenRouter default is restored**, green baseline (suite 1428 passed, 44 skipped).
- **Deliberate routing knobs**, not accidental alias behaviour: latency-sort by default, provider-pin for
  determinism, reasoning A/B toggle — all env, all documented, none in node/agent code (keeps ADR-0039 /
  model-profile-seam discipline).
- **A separate, pre-existing reliability gap is now visible and scoped, not fixed here:** the docling-graph
  extraction path still returns `"empty or all-null JSON"` on a meaningful fraction of clauses (≈5/7 even on real
  clauses; worse on template/placeholder fixtures). This appears on **both** candidate models, **sequentially and
  concurrently** (so it is neither the model choice nor this session's concurrency work), it is handled
  **losslessly** (the document goes PARTIAL, nothing is silently dropped), and the reasoning toggle does not fix
  it. Its cause is the server-side guided-decoding path (docling-graph forces `response_format` json_object /
  json_schema and JSON-parses the reply) — exactly the fragility that client-side tag-parse (ADR-0045) eliminates
  query-side. The fix is **TAGPARSE-INGEST-1** (move ingestion extraction to `build_tag_structured`), scheduled
  next, and A/B'd end-to-end against this baseline.
- **Reversible / retargetable:** the switch is `_PRODUCT_LLM` + the extraction default constants; a dev run still
  reaches any dropped foundation model via its real (still-registered) profile.
