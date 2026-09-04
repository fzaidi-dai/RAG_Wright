# ADR-0077: Function classification runs concurrently across chunks, bounded by one shared semaphore

Date: 2026-09-04
Status: Accepted (implemented; CLASSIFY-CONCURRENCY-1)

Part of straightening the ingestion concurrency design so every concurrency limit is a **deliberate knob**, not an
accidental serialization.

## Context

`_asegment_and_classify` classified chunks **sequentially** — `for ch in chunks: await
classify_fn.aclassify_spans(ch.text, …)` — so a document with M chunks paid M serial network round-trips, even
though nothing depends on chunk order. This was an oversight in the async port (the loop shape carried over from
the sync version), not a design choice. The calls run on the **async model seam** (`astream_text` on the event
loop), so this is independent of the thread pool (EXEC-1).

Within a chunk, `aclassify_spans` already ran its sub-batches concurrently under a private
`Semaphore(_SUBBATCH_CONCURRENCY)`. The naive fix — an outer semaphore around a chunk-level `gather` over that
same private semaphore — risks a nested-semaphore **deadlock** (a coroutine holding an outer permit waits to
acquire an inner permit that other outer holders own), and gives two multiplied knobs rather than one.

## Decision

Classify all chunks concurrently, bounded by **one shared semaphore acquired only at the leaf LLM call**.

- `_asegment_and_classify` segments every chunk (CPU/regex, sync), then `asyncio.gather`s the per-chunk
  `aclassify_spans` calls — no outer semaphore (the coroutines are cheap; only the leaf calls need bounding).
- `aclassify_spans` / `_aclassify_raw` take an optional `sem=`; when the caller passes one, every sub-batch call
  across every chunk acquires **that** semaphore, so the **total** in-flight classify calls are a single
  deliberate knob (`CLASSIFY_CONCURRENCY`, default 8, env-tunable). Because the semaphore is acquired only at the
  leaf `.ainvoke` — never held across another acquire — nesting the chunk gather over it cannot deadlock.
- `gather` preserves order, so the flattened output is identical to the sequential version.

## Consequences

- **Live-verified.** doc1 (5 chunks, real granite): serial (`max_concurrency=1`) 30.7s → concurrent
  (`max_concurrency=8`) 4.7s = **6.6×**, with byte-identical output (165 typed spans). The speedup is a property
  of our concurrency, independent of the model host's absolute latency.
- **One deliberate knob.** `CLASSIFY_CONCURRENCY` bounds the whole classify stage (across chunks and sub-batches);
  `max_concurrency=1` fully serializes, higher values scale up. No accidental machine-derived ceiling.
- **No deadlock, order preserved, degrade preserved.** The leaf-only acquire is deadlock-free; `gather` keeps
  output order; a failed/timed-out sub-batch still leaves its spans empty (unchanged).
- **Independent of EXEC-1.** This is the async-seam substrate; the thread-pool cleanup (EXEC-1) is separate.
- **No API/identifier/schema change** — an internal gather plus an optional `sem=` on the classifier seam
  (back-compat: existing callers that omit it get the prior per-call semaphore).
