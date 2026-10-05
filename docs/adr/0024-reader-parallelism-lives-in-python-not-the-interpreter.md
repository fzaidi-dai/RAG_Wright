# ADR-0024: OKF reader parallelism lives in a Python PTC tool, not sub-agent dispatch

> **Status: SUPERSEDED / RETIRED (ADR-0025, ADR-0046).** The FR-K embedding-free OKF navigation experiment was shelved by the retrieval pivot (ADR-0025) and retired when ACORD folded into one production KG (ADR-0046).


Status: accepted
Date: 2026-07-22

## Context

The `okf_navigate` traversal (T50, FR-K.6) reads and judges many concept bodies per query. The first design
made body relevance an `okf_reader` sub-agent, dispatched from the authored workflow via `task()`, and tried
to parallelize the reads with `Promise.all` in the interpreter (the fan-out-and-synthesize pattern).

It did not parallelize. tpb (budget 25) stayed at ~163s, indistinguishable from one-at-a-time (~174s), and
LoL (budget 50) ran past 15 minutes.

The cause is ADR-0020 / KI-1: one QuickJS engine per process, serialized by `rlm_interpreter_session`'s
`BoundedSemaphore(1)`. A workflow's `eval` holds the engine while it runs; `Promise.all` of `task()` schedules
the sub-agent dispatches but cannot overlap them, because each dispatch re-enters the same single-engine
machinery. The `task()` bridge being `async` does not help — the serialization is upstream of it. So sub-agent
fan-out from inside a live `eval` is inherently sequential, whatever the JS looks like.

## Decision

Body judging is a **Python PTC tool** (`tools.judgeBodies({rel_paths})`), not a sub-agent. The tool fans the
batch out in Python with `asyncio.Semaphore` + `asyncio.gather` + `asyncio.to_thread` — the same pattern
CLAUDE.md mandates and `embed_chunks` / `rlm_synthesis._combine` already use — so the concurrency runs on the
outer loop, off the single JS engine. Each judgment is forced through the profile seam
(`build_structured(model_id, Relevance)`), never a hardcoded provider flag.

The `okf_selector` judgment stays a `task()` sub-agent: it is low-volume (a handful of dispatches per query)
and needs no parallelism, so the single-engine serialization does not bite it.

General rule for interpreter-driven capabilities: **any judgment that must run at fan-out scale belongs in a
Python PTC tool that parallelizes with asyncio; reserve `task()` sub-agents for low-volume judgments.**

## Consequences

- Real intra-query parallelism: tpb 163s -> 110s (11/11 unchanged); LoL >15min -> ~5.8min.
- Structured output moves onto the seam. The seam applies DeepSeek's structured method and disables thinking
  on the forced call, so it retires the intermittent `tool_choice`-in-thinking 400s that `task(responseSchema)`
  hit (going around the seam, against the standing model rule).
- The reader is no longer an authored sub-agent; the dynamic-sub-agents paradigm is preserved for selection
  (the model still writes the workflow and dispatches the selector), while high-volume judging is a tool.
- The `judgeBodies` calls do not currently carry the Langfuse callback (`build_structured` builds its own
  model without forwarding callbacks); the orchestrator and selector remain traced. Revisit if per-judgment
  traces are needed.
- Latency floor is now the per-call model latency and the fixed orchestrator/selector cost, not the read
  count times a constant. Further latency wins come from reading fewer bodies (the T46 deep-hierarchy work),
  not more parallelism.

## References

- ADR-0020 (serialize interpreter sessions per process, KI-1) — the constraint this decision follows from.
- ADR-0006 (model-profile seam) / the standing model rule — why judging goes through `build_structured`.
- CLAUDE.md concurrency rule — `asyncio.Semaphore` + `to_thread` + `gather`, as in `embed_chunks`.
