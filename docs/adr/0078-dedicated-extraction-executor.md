# ADR-0078: A dedicated, env-sized executor for network-bound extraction (not the CPU-derived default)

Date: 2026-09-04
Status: Accepted (implemented; EXEC-1)

Completes the ingestion concurrency-design cleanup (with ADR-0077): make every concurrency limit a **deliberate
knob**, not an accidental byproduct of a Python default or the host's core count.

## Context

`asyncio.to_thread` uses the event loop's **default** executor, created lazily as `ThreadPoolExecutor(max_workers
= min(32, os.cpu_count() + 4))` — 16 on a 12-core box, 12 on the 8-core GCP box. We never configured it. That
`cpu+4` heuristic is right for **CPU-bound** blocking work, but our `to_thread` pool is dominated by
**network-bound docling-graph extraction** (clause / party / claim / requirement — all funnel through
`aextract_parties`, which spends its time waiting on the model host, not the CPU). So the default did two
unhelpful things:

- It **conflated** two workloads: extraction contended with genuine CPU work (parse, BGE encode, entity
  resolution, DB writes) in one undersized pool.
- It **capped extraction concurrency at ~core count**, non-reproducibly per machine, with nothing to do with what
  the model host can actually serve. The logical `CLAUSE_CONCURRENCY=8 × cross-doc max_concurrency=8 = 64`
  silently collapsed to ~16.

The model host (OpenRouter / Modal / local GPU) is a **tunable variable we control**, secondary to getting our own
substrate right — so the fix targets our design, independent of any provider's latency.

## Decision

Route the extraction offload to a **dedicated, env-sized `ThreadPoolExecutor`**, separate from the default pool.

- `extraction_executor()` — a lazy process-lifetime singleton, `max_workers = RAG_EXTRACT_WORKERS` (default 32),
  decoupled from `cpu_count`.
- `aextract_parties` (the single offload point for every docling-graph extraction) uses
  `loop.run_in_executor(extraction_executor(), …)` instead of `asyncio.to_thread`.
- Genuine CPU work (parse / embed / resolve / DB writes) stays on the default `cpu+4` pool, where that heuristic
  is correct.

So extraction concurrency is now bounded by **our semaphores** (`CLAUSE_CONCURRENCY`, the cross-doc
`max_concurrency`) and the deployment — a deliberate knob — never an accidental machine-derived default.

## Consequences

- **Ceiling is ours, verified.** 24 concurrent extractions run at once through the real `aextract_parties` path
  (impossible on the ~16 default), sized by `RAG_EXTRACT_WORKERS`.
- **Real, measurable gain.** Live A/B on real granite (doc1, 87 clauses, logical concurrency 32): lifting the
  executor 16 → 32 gave **1.29×** (74.8s → 57.9s). Less than the theoretical ~2× because at 32-way the *provider*
  (OpenRouter latency/rate-limit) becomes the damping factor — the separately tunable variable, not our design;
  a better-provisioned or local host realizes more.
- **Workloads decoupled.** Extraction no longer starves, and no longer steals slots from, the CPU work on the
  default pool.
- **Deadline/degrade unchanged.** The docling-graph LLM call's true wall-clock deadline lives inside the worker
  thread (its deadline-bounded litellm client); which executor runs the thread doesn't change that.
- **Further, out of scope here:** run extraction as true async on the loop (docling-graph's inner call is already
  `litellm.acompletion`), removing the thread hop entirely — a larger change to docling-graph's sync
  `run_pipeline` wrapper.
- **No API/identifier/schema change** — an internal executor plus one re-routed offload; `RAG_EXTRACT_WORKERS` is
  the new (optional) knob.
