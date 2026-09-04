# ADR-0076: The shared BGE model (embedder + reranker) is serialized across threads

Date: 2026-09-04
Status: Accepted (implemented; engine issue 0016, filed by RuleWright)

## Context

Ingesting two or more documents concurrently **segfaulted the interpreter** (SIGSEGV, not a catchable exception).
`FlagEmbedding` mutates its model **in place on every `encode`** (`self.model.float()` / `.to(device)` /
`.eval()`), and those C++ conversions release the GIL — so two threads driving one shared `BGEM3FlagModel` swap
the same parameter-tensor storage under each other and crash the process (fault frame: `torch module._apply` →
`convert`, inside `.to()`). The engine SHARES a single `Embedder` instance and had **no lock** (only an
`asyncio.Semaphore` for backpressure, which is not thread mutual exclusion).

Two exposed call sites, both real:
- **`encode_batch`** — one call per document (`contract_ingestion_pipeline` `index_fn`, via `to_thread`). Safe
  sequentially, but `async_ingestion.run_job(max_concurrency=8)` — the engine's own advertised default — runs
  multiple documents' `index_fn` concurrently on the shared embedder → race.
- **`encode_dense`/`encode_sparse`** — `embed_chunks` fans these out through `to_thread` at
  `DEFAULT_MAX_CONCURRENCY=4`, racing the shared model within a *single* document.

Control: document concurrency 1 passes; 3 segfaults. RuleWright cannot work around it (the product never calls
`encode_batch`; the race is deep in the engine ingest graph, and per ADR-0052 the product does not patch the
engine). NFR-4 (bulk ingest at corpus scale) was blocked on it.

## Decision

Serialize every model call behind a **per-instance `threading.Lock`**.

- `BGEM3Embedder` holds `self._lock`; `encode_dense`, `encode_sparse`, and `encode_batch` wrap only the
  `self._model.encode(...)` call in it (the returned numpy arrays are per-call and thread-local, so the
  `.tolist()` / dict conversions stay outside the lock — the serialized section is just the encode).
- `BGEReranker` gets the same lock around `self._model.compute_score(...)` — defense-in-depth for any concurrent
  `score` caller (the same in-place-mutation class).
- Both constructors gain an optional `model=` injection point so the mutual-exclusion behavior is testable
  without loading the real model.

**Per-instance, not global:** the race is on one shared model's tensors; separate instances have separate
storage, so a global lock would only over-serialize (a query embed blocking an ingest embed).

**Lock over pool:** a per-thread or K-model pool would overlap embedding but costs ~2–4 GB RAM per instance and
does not help CPU-bound encode (device contention). Embedding is a small slice of ingest time; the concurrency
worth overlapping is the network-bound extraction (minutes), not the encode (seconds). RuleWright explicitly
accepts serialized embedding. An uncontended lock costs nothing sequentially.

## Consequences

- **Fixed, live-verified.** The real shared BGE-M3 under 80 concurrent `encode_batch` calls across 4 threads
  completes with **no segfault** and bit-stable vectors (the exact race that died at document concurrency 3).
  Hermetic tests prove serialization (`max_in_flight == 1`; verified non-vacuous — disabling the lock gives 8)
  for both the embedder and the reranker.
- **NFR-4 unblocked.** Document-level ingestion concurrency (`run_job(max_concurrency>1)`) is now safe.
- **Throughput.** Embedding is serialized across threads by design; negligible, since it is CPU/GPU-bound and a
  small fraction of per-document time, and the network-bound extraction stages still overlap.
- **No API/identifier/schema change** — an internal lock plus an optional test-only `model=` parameter.
