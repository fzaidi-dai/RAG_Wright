# Handoff to RuleWright — engine issue 0016 fixed: the shared embedder/reranker is now thread-safe (ADR-0076)

Date: 2026-09-04. From: RAG_Wright engine. Re: your issue 0016 ("the shared embedder is not thread-safe and
segfaults under document concurrency"). **Resolved** — your diagnosis was exactly right, down to the mechanism.
Detail in `docs/adr/0076-*.md`.

## What we confirmed

Everything in your report checks out against the code: one shared `BGEM3FlagModel`, no lock, `encode_dense`/
`encode_sparse`/`encode_batch` calling `self._model.encode` directly, and the `asyncio.Semaphore` being async
backpressure — not thread mutual exclusion. FlagEmbedding's per-encode in-place `.float()`/`.to()`/`.eval()`
release the GIL, so two threads racing them swap the same tensor storage → SIGSEGV. Both call sites you named are
exposed: `encode_batch` under `run_job(max_concurrency=8)`, and `embed_chunks`' 4-thread fan-out within one
document.

## What changed in the engine you consume

**`Embedder` (and the reranker) is now safe to call from multiple threads.** A per-instance `threading.Lock` is
held across each `self._model.encode(...)` and `compute_score(...)`. Exactly the smallest fix you asked for.

- **Effect:** document-level ingestion concurrency is safe — `run_job(max_concurrency>1)` no longer segfaults.
  Embedding is serialized across threads (as you noted, acceptable — the stages you want to overlap are the
  network-bound extraction calls, not the CPU-bound encode), so throughput of the concurrency you care about is
  unaffected.
- **Verified on the real model:** 80 concurrent `encode_batch` calls across 4 threads on one shared BGE-M3 →
  no crash, bit-stable vectors (your doc-concurrency-3 repro).

## Actions on your side

- [ ] **Bump the engine version.** No breaking API / identifier / schema change (the only new surface is an
      optional test-only `model=` constructor arg).
- [ ] **Turn document concurrency back on.** You held it at 1 as the workaround — you can now raise
      `max_concurrency` on `submit_ingestion` / `run_job`. This is your NFR-4 lever.
- [ ] Heads-up: embedding is serialized, so raising document concurrency overlaps the extraction (network-bound)
      stages, not the encode — which is the win you were after. Expect throughput to scale with the extraction
      overlap, not the embed.
