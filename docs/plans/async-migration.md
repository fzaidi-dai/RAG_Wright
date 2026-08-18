# Async engine migration — plan (ASYNC-* arc)

Phase-1 plan for the full async conversion decided in ADR-0057 (the real fix for engine issue 0003). Bottom-up,
each task TDD with a review gate; `main` stays green throughout. Deadline constant `_MODEL_DEADLINE_S = 180s`.

> Direction (locked): full async end to end, true `asyncio.timeout` cancellation, `astream` +
> `stream_chunk_timeout` for free-text, docling-graph via injected async client (Approach A, no fork), NO sync
> shim (scripts use `asyncio.run(main())` entrypoints), `pytest-asyncio` added. Blast radius accepted.

## Guarantees to preserve at every step
Bounded transient retries + per-attempt logging (ADR-0056); no-claim-without-a-citation and the generation
guarantees (ADR-0053/54/55); the classifier empty-sub-batch degrade and the pipeline dead-letter paths; content
-hash gating and resume (`is_done`); tracing/Langfuse (contextvars propagate through asyncio natively); ordering
/determinism (`gather` preserves order).

## Phase A — async seam + deadline (foundation)
- **ASYNC-A1 — test infra.** `uv add pytest-asyncio`; configure asyncio mode; a hermetic fixture pattern for
  fake async runnables. *Verify:* an async test runs; suite green.
- **ASYNC-A2 — async seam + true deadline.** `build_structured`/`build_model` async path (`AsyncOpenAI` via
  `.ainvoke`); async `_with_bounded_retry` (bounded transient retries + per-attempt logging preserved); wrap each
  logical call in `asyncio.timeout(_MODEL_DEADLINE_S)` → terminal `ModelCallTimeout`. *Verify:* a fake
  slow-drip coroutine is **cancelled at the deadline** (socket-close semantics), transient retried N times,
  non-transient not retried, timeout terminal + logged.
- **ASYNC-A3 — free-text streaming.** `astream` + `stream_chunk_timeout` idle detection for the free-text /
  tag-parse path (`SeamReasonModel`, `TaggedFreeTextAnswerModel`, `tag_structured`); accumulate to the same
  result contract. *Verify:* an idle stream (no chunk within the chunk-timeout) raises; a normal stream yields
  the full text.
- **ASYNC-A4 — docling-graph on our seam (Approach A).** A custom client injected via `PipelineConfig.llm_client`
  whose `get_json_response(_stream)` routes through the async seam under `asyncio.wait_for`; `extract_parties`/
  `extract_clause` become async (`run_pipeline` via `asyncio.to_thread`, LLM socket truly cancellable inside the
  injected client). *Verify:* extraction still returns the same contract; a stalled docling-graph LLM call is
  cancelled at the deadline with the worker thread finishing at N (no leak); no docling-graph fork.

## Phase B — async ingestion pipeline
- **ASYNC-B1 — async classifier.** `classify_spans` async; concurrent sub-batches via `asyncio.gather` +
  `Semaphore` (was `ThreadPoolExecutor`); degrade path preserved.
- **ASYNC-B2 — async ingestion nodes.** `chunk/segment/extract_clauses/index_spans/extract_graph/resolve/write`
  → `async def`; docling parse + local embedding via `asyncio.to_thread`; remote embedding/classify via
  `httpx.AsyncClient`.
- **ASYNC-B3 — async graph + orchestration.** per-document graph, `run_corpus_ingestion`,
  `submit_ingestion`/`run_job` → `ainvoke`; `ModelCallTimeout` terminal in the pregel `retry_on`; dead-letter /
  resume / partial preserved. *Verify:* a document whose classify times out degrades (empty sub-batch), ingests,
  and is not pregel-retried.
- **ASYNC-B4 — async store I/O.** ArcadeDB reads/writes non-blocking (async client or `asyncio.to_thread`) so a
  slow DB call does not stall the loop. **MUST also harden `JobStore`** (engine issue surfaced in B2c): `get`
  reads a job file that may be empty mid-write -> occasional `ValidationError` under parallel load. Fix with an
  atomic write (write-temp-then-rename) and/or a tolerant read (treat empty/partial as "not ready"). Do NOT drop
  this -- it is a real robustness gap on the product's async job path.

## Phase C — async query side
- **ASYNC-C1 — async query subgraphs.** `intra_document_qa`, `relational_qa`, `typed_property_retrieval`,
  `compliance_check` → async.
- **ASYNC-C2 — async generation + encoders.** `answer_generator` single/reasoned/best-of-n via `asyncio.gather`;
  query-side remote encoders async.
- **ASYNC-C3 — async MCP servers.** FastMCP tool handlers `async def` awaiting the async subgraphs.

## Phase D — entrypoints, no sync shim
- **ASYNC-D1 — convert scripts/CLIs.** async `main()` + `asyncio.run(main())` entrypoints; **remove** sync
  wrappers (`embed_chunks_sync`, etc.).
- **ASYNC-D2 — engine public async API + ARD.** finalize the async ingest/query/compliance entrypoints and their
  ARD registrations; document the interface contract for the product.

## Phase E — RuleWright product (its own session/repo, per ADR-0052)
- **ASYNC-E1 — async FastAPI.** product routes → `async def` awaiting the engine's async entrypoints; removes the
  anyio 40-token thread-limiter exposure. Engine defines the contract (ASYNC-D2); code lands in the RuleWright
  session.

## Risks / handling
`pytest-asyncio` (ask-first: approved). CPU/sync libs (docling, local embedding, docling-graph orchestration)
wrapped in `asyncio.to_thread`; docling-graph's LLM socket truly cancelled via the injected client (A4).
Determinism via ordered `gather`. Each phase gated + green so a partial migration never lands broken. Issue 0003
stays open until ASYNC-A/B land and RuleWright re-runs `repro_ingest_deadlock.py` (expect the tail bounded at
~180s and the silent-success case caught).
