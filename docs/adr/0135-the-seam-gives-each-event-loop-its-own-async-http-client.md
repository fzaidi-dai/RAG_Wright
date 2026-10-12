# ADR-0135: The model seam gives each event loop its own async HTTP client

**Status:** accepted · **Date:** 2026-10-12 · **Related:** ADR-0056/0057 (the bounded retry and the async
deadline), EV-0 finding 5 (`docs/specs/evals/plan.md`), PS-24

## Context

langchain_openai's `ChatOpenAI` takes its async HTTP client at construction, and by default shares one cached
client per base URL and timeout across the whole process. httpx's pooled connections belong to the event loop that
opened them. The seam builds runnables once and calls them later, possibly on another loop: Langfuse's
`run_experiment` runs every experiment on a fresh loop, and a web backend has its own loop beside a worker's. EV-0
saw the first calls of the second run fail at once with `APIConnectionError` (`Event loop is closed` underneath);
the seam's retry recovered them, at the cost of a retry, and a burst at the start of a run could use up the
retries.

## Decision

`build_model` passes `ChatOpenAI` an `httpx.AsyncClient` (`_LoopLocalAsyncClient`) that sends each request through
a client of the running event loop. That client is built the way langchain_openai builds its default (its own
builder and socket options), kept per loop in a weak map so it goes with the loop, and the wrapper is cached per
base URL and timeout, so calls on one loop still share their connections. A caller's own `http_async_client` wins,
and with `OPENAI_PROXY` set langchain_openai keeps building its proxied client.

## Consequences

- Async calls work on every event loop in a process, with no retry spent; synchronous calls are unchanged.
- The seam now imports two private langchain_openai helpers (`_build_async_httpx_client`,
  `_resolve_socket_options`), so the per-loop client matches the default exactly; a langchain_openai upgrade that
  moves them fails at import, and the tests in `tests/models/test_seam_event_loops.py` show it.
