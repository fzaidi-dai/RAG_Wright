# RuleWright handoff: engine issues 0018–0023 resolved (+ qwen3.8 query profile)

Date: 2026-09-09 · Engine commits: `08d048f` (qwen profile), `f8cbb61..9f74c7f` (pushed to origin), `3d6fab0` (0023, local — build against the local checkout)

This batch closes the observability/cost arc and the retrieval relevance-floor gap. Most of it is transparent to you; three items need a decision or an action on your side, flagged **ACTION** / **DECISION**.

## What changed

- **0018** — trace context now survives the ADR-0078 executor hop, so `dg_extraction` generations correlate under your `traced_run` session (was `sessionId: null`). Per-query and per-document cost are now attributable by session, including concurrent users.
- **0019** — docling-graph's gleaning pass (a 2nd LLM call) is off on the query leg; it was near-useless there (redundant restatement) and doubled cost/latency. Ingestion keeps it.
- **0020** — query constraint extraction moved to client-side tag-parse over the same `Clause` (ADR-0045): **~68% fewer input tokens, one call.** Product API unchanged (`production_typed_property_retrieval(extract_model=…)` still works; the model id is derived from `extract_model.model`).
- **0021** — the `astream_text` (streaming) path now recovers OpenRouter's **real per-call cost**, which LangChain's streaming normalization was dropping. → **no more $0.00 / UNPRICED for a newly adopted model.**
- **0022** — `_scrub_prose` repairs the `,.` residue left when a citation list is stripped, so answer prose no longer shows machine artifacts.
- **0023** — `RankedSpan` now carries `retrieval_score` (the fused RRF relevance that ordered the pool), so `not_found` is reachable.

## What you need to do / decide

- **DECISION (0023): the relevance floor is yours to set.** `retrieval_score` is a float on every `RankedSpan` (propagates via `TypedPropertyRetrieval.results`), `None` only if the backend surfaces no score. It is the **fused RRF score**, not a raw cosine — I chose it on measurement: raw cosine is furniture-dominated on the corpus I had (~3% margin), while RRF separated cleanly (on-point top ~0.0315 with a descending profile vs a nonsense query flat at ~0.0164). Tune the matched/possible/not_found cut on your **clean** corpus — my numbers came from the stale `ragwright_cuad_full` and are directional only. If your retest finds cosine would floor better for you, say so; it's a small follow-up.
- **ACTION (0021 pricing): you can drop the hand-maintained Langfuse price rows** for models on the streaming path — real cost now arrives as `cost_details`. Keep a row only as a fallback for a backend that surfaces no cost (e.g. self-hosted vLLM).
- **Available (qwen3.8): `qwen/qwen3.8-27b` is profiled for the query side** — pass it as `judge_model_id` / `answer_model_id` / `extract_model`. The judge reasons deeply (~21–32s/pair, deliberate); query extraction runs reasoning-off (deterministic, cheaper) via the new `text_extra_body` free-text control. Both settings live in the profile, not your call sites.

## Retest note for 0023

Reproduce your `purple elephants…` sweep on a current corpus and check `retrieval_score` on the returned spans: a nonsense query should sit near the flat RRF floor while a real match rises above it. Pick your threshold from that spread. The engine deliberately does not pick it.

Reference: ADRs 0083–0087, `docs/OBSERVABILITY.md`, and the commit range above.
