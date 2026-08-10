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

## Addendum (PROD-3 hard requirement): lossless ingestion — NO silent partial success

PROD-1/PEXT-1 surfaced an unacceptable production behavior: a stage can fail *inside* a document (e.g. party
extraction returns truncated JSON on a transient LLM garble), the pipeline **degrades gracefully to empty** (0
parties), the document is still written as "ingested", and the loss is only discoverable by grepping logs after
the fact. For a production application this is a silent data-loss defect, not resilience.

**Invariant (non-negotiable):** every document either ingests **fully** — all REQUIRED stages succeed (retried on
transient failure) — **or is explicitly recorded** as failed, never silently written with missing extractions.
Concretely the async ingestion job MUST:
1. **Track per-stage outcome per document** (parse / chunk / segment / classify / clause-extract / party-extract /
   write), so a partial is detectable, not swallowed. A stage that "degrades to empty" MUST set a failure flag,
   not pass silently.
2. **Retry transient failures** — per-node `RetryPolicy` (the LangGraph lever) with backoff, since the observed
   failures are concurrency/provider transients that succeed on a clean retry.
3. **On irrecoverable failure of a required stage → DEAD-LETTER the document** (do not commit a partial/misleading
   Contract), OR write it explicitly flagged `PARTIAL` with the failed stages + reason recorded on the node — the
   choice is per-stage, but the outcome is always **visible**, never silent.
4. **Surface it in the job status:** `get_ingestion_status` reports `dead_lettered[]` and any `partial[]` with
   reasons, so a failure is known at job-completion time, not discovered later.
5. **Idempotent re-run heals it:** because `is_done` + content-hash gating already make re-ingest re-do only the
   unfinished/failed work, a dead-lettered document can be re-submitted and completed without touching the rest.

This invariant is the PRIMARY acceptance criterion for PROD-3 — a stable, lossless-or-explicitly-dead-lettered
pipeline — above raw throughput. PEXT-1's transient is the first concrete case it must handle correctly.
