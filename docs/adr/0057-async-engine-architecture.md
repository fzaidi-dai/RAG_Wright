# ADR-0057: Async engine architecture (true wall-clock cancellation, no sync pipeline)

Status: Accepted (foundational; supersedes the ADR-0056 per-attempt bounding as the real fix for issue 0003)
Date: 2026-08-18
Component: the whole engine — `models/seam.py`, the ingestion and query pipelines, the MCP servers, the
entrypoints, and the RuleWright product interface. Raised by: RuleWright (product), engine issue 0003 (reopened).
Related: ADR-0056 (bounded per-attempt retry — necessary but insufficient), ADR-0006 (single model seam),
ADR-0039 (self-hosted substrate), ADR-0052 (engine/product boundary).

## Context

Issue 0003 reopened after ADR-0056: the retry-layer collapse was correct, but the bound still did not hold. An
httpx timeout is **per socket operation, not a total deadline** — a slow-drip response (a byte, or an OpenRouter
SSE keep-alive comment, every < timeout seconds) resets the read clock indefinitely. RuleWright measured a single
"attempt" running 591s against a 60s timeout, and — the dangerous case — a 399s call that **succeeded with no
exception, no retry, and no log line at all**. Every existing safety mechanism (retry budgets, transient sets,
dead-lettering, the classifier's empty-sub-batch degrade) keys off an **exception that is never raised**. Only
elapsed wall clock measured **outside the socket layer** can bound it.

In synchronous Python you cannot inject an exception into a thread blocked in a C-level socket read, so a thread
watchdog is only a *soft* cancel (the orphaned read runs to completion, leaking the thread). The correct fix is
**async**: `AsyncOpenAI`/`ChatOpenAI.ainvoke` are real `await` points, so `asyncio.timeout(N)` raises
`CancelledError` into the coroutine, httpx **closes the socket**, and the call is truly cancelled at N. This is
also the right foundation for an engine that must scale: a synchronous pipeline is a demo-grade choice.

Grounded async support is present end to end: `CompiledStateGraph.ainvoke/astream`, `ChatOpenAI.ainvoke/astream`
(with a native `stream_chunk_timeout` for idle-between-chunks detection), `AsyncOpenAI`, and `asyncio`.

### Sync surfaces (grounded, definitive)

| Surface | Status | Handling |
|---|---|---|
| our model seam (`build_structured`/`build_model`) | sync `.invoke` | → async `.ainvoke` + `asyncio.timeout` (true cancel) |
| docling `DocumentConverter.convert` | sync, no async methods | `asyncio.to_thread` (CPU/IO-bound; loop non-blocking) |
| local embedding `FlagEmbedding.encode` | sync (torch) | already wrapped in `asyncio.to_thread` by `embed_chunks` |
| remote embedding/classify (`remote_encoders`) | sync HTTP (`urllib`) | → `httpx.AsyncClient` (genuine async I/O) |
| docling-graph `run_pipeline` | sync, v1.9.1, no async variant; uses litellm internally | **Approach A** (below) |
| ArcadeDB store client | I/O-bound (to confirm in Phase B) | async client or `asyncio.to_thread` |

## Decision

Convert the engine to **async end to end**, with a true wall-clock deadline on every model call.

1. **Async model seam + deadline.** Model calls go through `.ainvoke`; every logical call is wrapped in
   `asyncio.timeout(_MODEL_DEADLINE_S = 180s)` → a **terminal** `ModelCallTimeout` (never pregel-retried; a
   stalling peer is not a transient worth re-hitting). The bounded transient retries and per-attempt logging from
   ADR-0056 are preserved in an async retry wrapper.
2. **Streaming for free-text.** Free-text / tag-parse calls (ADR-0045) use `astream` + `stream_chunk_timeout` for
   precise idle-drip detection and progressive output; forced-structured (tool-call) calls use `ainvoke` + the
   total deadline (tool-call output does not parse incrementally).
3. **docling-graph via Approach A (no fork).** docling-graph exposes `PipelineConfig.llm_client` / `get_client`
   as a client-injection seam. We inject a custom client whose `get_json_response(_stream)` routes through our
   async seam under `asyncio.wait_for`; because `run_pipeline` runs in `asyncio.to_thread`, the inner
   `asyncio.run(wait_for(...))` truly cancels the socket at the deadline, so the worker thread finishes at N
   (no leaked thread). This unifies docling-graph's LLM calls onto the same seam as everything else (one deadline
   / retry / logging / tracing policy). We do NOT fork or vendor docling-graph.
4. **No sync shim.** The library has no synchronous code paths. Scripts/CLIs use `asyncio.run(main())` as their
   process entrypoint (the one legitimate boundary); `embed_chunks_sync` and any other sync wrappers are removed.
5. **Concurrency.** `ThreadPoolExecutor` / `map_concurrent` loops become `asyncio.gather` + `asyncio.Semaphore`
   (native form of the standing async+semaphore rule); `gather` preserves order, so determinism holds. CPU-bound
   steps (docling parse, local embedding) go through `asyncio.to_thread`.
6. **Product (RuleWright).** FastAPI routes become `async def` awaiting the engine's async entrypoints — which
   also removes the anyio thread-limiter exposure (async routes consume no thread-pool token). Done in the
   RuleWright session per ADR-0052; this ADR fixes the interface contract.

## Consequences

- A single stalled/slow-drip call is bounded at 180s by true socket-teardown cancellation, on every path
  including docling-graph; the silent-success case is caught because the ceiling is wall clock, not an exception.
- The engine is async-native and scalable; the product sheds its thread-limiter risk.
- Larger blast radius (accepted, per the engine owner's explicit direction): nearly every capability, subgraph,
  entrypoint, and test is touched. Migration is bottom-up and gated so `main` stays green throughout.
- New dev dependency: `pytest-asyncio` (hermetic async tests).
- `ModelCallTimeout` is terminal — a genuinely slow-but-progressing legitimate call that exceeds 180s is
  cancelled and degrades/dead-letters; 180s is set well above the ~11s median with headroom.

## Migration

Sequenced bottom-up in `docs/plans/async-migration.md`, tracked as the `ASYNC-*` arc in `tasks.md`: Phase A
(async seam + deadline + streaming + docling-graph client), Phase B (async ingestion), Phase C (async query +
MCP), Phase D (async entrypoints, remove sync shims), Phase E (RuleWright FastAPI async, its session). Each phase
ends with a review gate and a green suite.
