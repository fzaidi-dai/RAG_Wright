# ADR-0027: Route DeepSeek V4 Pro by OpenRouter throughput (provider flag in the profile)

Status: accepted (2026-07-24)
Relates to: ADR-0006 (the model-profile seam; provider flags live in the profile config + a dated ADR),
the model rule (DeepSeek V4 Pro is the STRUCTURED_REASONING default).

## Context

The T58 bulk property extraction (~3,900 DeepSeek V4 Pro structured calls) stalled under sustained
concurrency: a single call returned in ~5s, but 8 concurrent calls throttled to ~20s each and the run
crawled (~6h ETA), while the exact same workload had run fine at concurrency 8 the day before. This is
OpenRouter's default routing: it load-balances a model across providers preferring the CHEAPEST that is up,
which can land the whole burst on a slow/throttled provider.

OpenRouter exposes provider routing in the request body. Grounded against OpenRouter's docs and the installed
`langchain_openai`: `extra_body={"provider": {"sort": "throughput"}}` makes the router prefer the fastest
provider (disabling cheapest-first load-balancing); `extra_body` is a real `BaseChatOpenAI` field intended for
exactly these non-OpenAI parameters (base.py:927). This keeps DeepSeek V4 Pro (model rule intact) and only
changes which provider serves it.

## Decision

Add a base `extra_body` to `ModelProfile`, applied by `build_model` to the BASE client (every call to that
model), distinct from the existing `structured_extra_body` (forced-structured call only). Set the DeepSeek V4
Pro profile's `extra_body = {"provider": {"sort": "throughput"}}`.

This follows the standing rule: a provider flag is empirical and lives in the model-profile config + a dated
ADR, never in node/agent code. No call site changes; a model without a base `extra_body` is unaffected.

## Consequences

- The property extraction (and every other DeepSeek V4 Pro call) routes to the fastest provider, the mitigation
  for the throttle, without switching models or touching capability code.
- Base `extra_body` and `structured_extra_body` are independent; DeepSeek V4 Pro has only the base one (no
  thinking-disable needed), so there is no merge conflict. A model needing both would require merging them at
  the seam -- deferred until such a case exists.
- `"throughput"` trades a possible small price increase for latency/reliability; acceptable for our bulk
  ingestion. `"latency"` is the alternative sort if tail latency ever matters more than throughput.
- Effectiveness at scale is empirical (a provider flag): confirmed cheaply by the extraction run itself, not
  asserted here.

## References

- OpenRouter provider routing (`sort`: throughput / latency / price); `langchain_openai` `extra_body`
  (`chat_models/base.py:927`). Tests: `tests/models/test_profile_seam.py`
  (`test_base_extra_body_binds_to_the_base_client`, `test_deepseek_v4_pro_profile_routes_by_throughput`).
