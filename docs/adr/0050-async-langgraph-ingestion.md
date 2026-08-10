# ADR-0050: Async, LangGraph-based ingestion (job submit + status, not a blocking run)

## Context

`run_corpus_ingestion` is a synchronous, blocking for-loop over documents (PROD-1). That is fine for a dev/CI
smoke, but a production onboarding cannot make a user wait hours with a spinner: ingestion must be **submitted**
(returns immediately with a job id), run **asynchronously**, and its progress **polled via an API**, with proper
queuing and document-level parallelism. This is the production-grade uplift the ingestion pipeline needs.

Two facts make this tractable rather than a rewrite:
1. **The pipeline is already idempotent + resumable.** Every expensive stage is content-hash gated; `is_done`
   (a present `Contract` node = the whole document landed) skips finished documents; writes are upserts. This is
   exactly what durable/resumable execution requires.
2. **Documents are independent units of work** — the natural unit of parallelism and of a queue message.

And we already use **LangGraph** for essentially every LLM workflow (the per-document `production_document_ingest`
IS a LangGraph subgraph). LangGraph natively provides the async-job primitives (confirmed via the LangChain docs
MCP): a **checkpointer** persists state at super-step boundaries keyed by `thread_id`, giving durable, resumable
execution ("resume days later, picking up where it left off"); **parallel nodes / fan-out** run concurrently in a
super-step, bounded by `max_concurrency` in config (the rate-limit lever) with a per-node **retry policy**;
`interrupt()` + `Command(resume=...)` give pause/cancel. LangGraph's own resume guidance ("design nodes idempotent;
use upserts / read-before-write") is precisely what our pipeline already does — so wrapping it in a checkpointed
graph gives durable resume essentially for free.

## Decision

Model **corpus ingestion as a durable, async LangGraph job**:

- **A corpus-level LangGraph graph** whose nodes fan out documents (map) to the existing per-document ingest
  subgraph, bounded by `max_concurrency` (respects OpenRouter/granite rate limits), with a per-node retry policy;
  a fan-in node finalizes (party linking, report). This also removes today's sequential-documents bottleneck.
- **`thread_id = job_id`, a checkpointer for durable state.** A crashed/cancelled/redeployed run resumes from the
  last checkpoint; combined with `is_done` + content-hash gating, resumed runs re-do only what's missing.
- **Progress is derived, not held in the request:** an `IngestionJob` record `{job_id, corpus_ref, db, status,
  total, done, dead_lettered[], timestamps, error}` plus the KG itself as ground truth (`count(Contract)` for the
  corpus ÷ total) — robust to worker restarts.
- **Exposed as MCP tools** `submit_ingestion(corpus_ref, db) -> job_id` and `get_ingestion_status(job_id)`,
  consistent with how we already expose capabilities (compliance_server, the 4 query tools). The GraphWright
  orchestrator/agent *calls* these and polls status; it does not implement them.
- **Cancel/pause via `interrupt()`** on the job thread.

### MVP vs full (same job model)
- **MVP (PROD-3):** the corpus graph + a persistent checkpointer (SQLite/Postgres — build-time choice), run as a
  **background async task** in-process; a thin status read (checkpoint + KG). Validated on a **handful of documents**.
- **Full:** swap the in-process runner for a real queue — **LangGraph Platform/Server** (built-in background runs
  + REST status API + task queue) OR **GCP Pub/Sub + Cloud Run workers** with document-level messages (horizontal
  autoscale, DLQ, Langfuse observability we already wire). The `IngestionJob` + MCP surface is unchanged.

### Two-halves placement
This is **capability infrastructure (RAG_Wright)**, exposed via MCP — not orchestration. It keeps the boundary
clean: the product's agents submit and poll; the async ingestion job lives on our side.

## Consequences

- Non-blocking onboarding: submit → job id → poll; the UI never blocks on a multi-hour run.
- Document-parallel throughput (bounded for rate limits) — faster than today's sequential loop.
- Durable resume for free (checkpointer + our existing idempotency), so crashes/redeploys don't restart from zero.
- Build-time grounding required before coding (CLAUDE.md rule): the exact LangGraph fan-out API (`Send` /
  parallel-edges + `defer` fan-in), checkpointer backend, `max_concurrency`/`RetryPolicy` wiring, and the
  MVP-runner-vs-LangGraph-Platform decision. Tracked as **PROD-3** in `tasks.md`.
- Sizing (worker pool / queue concurrency) should use PROD-1's real per-document throughput + rate-limit ceilings.
