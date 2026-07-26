# ADR-0031: Single-call boundary discovery (+ deterministic repair) for structured-contract chunking

Status: accepted
Date: 2026-07-26
Related: ADR-0020 (RLM interpreter process-lock), ADR-0025 (retrieval architecture), ADR-0029 (portability),
FR-I.1 (deterministic chunking), CU-B4.

## Context

The CUAD highlight pipeline (CU-B4) must chunk hundreds of held-out contracts into clause-level chunks whose
character offsets are load-bearing for citation. Our existing chunker is the **agentic RLM**
(`SeamBoundaryDiscoverer`): a code-interpreter workflow with a sub-agent orchestrator and a coverage-tail that
*guarantees* a valid partition. It is our general chunker for messy/unstructured documents.

A pre-flight comparison (`scratchpad/chunk_model_compare.py`) measured boundary discovery on held-out CUAD
contracts across DeepSeek Pro / Flash / Gemma (agentic) and a single non-agentic structured call (Gemma),
scoring **clause integrity** (do CUAD gold answer spans stay within one chunk?), latency, and validity:

| approach | latency / contract | clause integrity | notes |
|---|---|---|---|
| single non-agentic call (Gemma) | **~3.4s** | **1.0** | 8–12 sensible clause chunks; 1/3 returned an invalid partition |
| agentic RLM (Gemma) | **>5 min** | — | did not finish; interpreter + multi-call orchestration |
| agentic RLM (Pro) | **>9 min** | — | did not finish; Pro throttle × `max_retries=6, timeout=120` |

The bottleneck is the **agentic workflow itself** (interpreter startup, multi-call orchestration, coverage
retries), not the model — Gemma-agentic is as slow as Pro-agentic. For CUAD's well-structured contracts a
single structured call gives equal-or-better clause integrity at ~100× less cost/latency. Its one weakness is
that a single call (unlike the agentic coverage-tail) does **not** guarantee a clean partition.

## Decision

1. **Add `SingleCallBoundaryDiscoverer`** (`capabilities/rlm_chunking.py`): one `build_structured` call
   (GENERAL role, Gemma-4-class) returning `{start_index, end_index}` spans, plugged into the existing
   `chunk()` via the `BoundaryDiscoverer` seam. It reuses the entire pipeline (`_finalize_chunks`, CU-B1
   offsets, content-hash gate); **the agentic `SeamBoundaryDiscoverer` is unchanged** and remains the default
   for messy/unstructured documents. Choice of discoverer is per-corpus, not global.
2. **`repair_partition`** replaces the agentic coverage-tail for the single call: it takes ANY set of ranges
   and derives a valid partition from the span **starts** in `(0, n)` as break-before points — always
   contiguous, gap-free, covering every item, ordered, non-empty. Robust to overlap / gap / out-of-range /
   `start>end` / unordered / duplicate model output; no valid break → one whole-document chunk. Tested
   exhaustively (17 tests incl. a 3000-iteration randomized property test) as the gate before any full ingest.
3. **Concurrency is ordinary async, not a process pool.** The single call has **no interpreter**, so the
   process-wide lock of ADR-0020 does not apply. CUAD ingestion (`scripts/ingest_cuad.py`) parallelizes the
   chunk calls with `asyncio.Semaphore` + `asyncio.to_thread` (Phase 1), then does local
   segment→classify→embed→store sequentially (Phase 2, shared non-thread-safe models/store).

## Consequences

- **CU-B4 full holdout:** 102 contracts, 27,074 spans, **offset round-trip clean on every span**
  (`canonical[doc_start:doc_end] == span.text`), 0 failures; Phase 1 chunking 156s at concurrency 6 (vs the
  hours the agentic path would have taken). The citation invariant that CU-B1/CU-B2 established holds at scale.
- **Determinism (FR-I.1):** the boundary call is a structured LLM call (not bit-deterministic), but the
  content-hash gate makes it chunk-once, and `repair_partition` makes the *validity* of the partition
  deterministic regardless of model output. This is the same posture as the agentic chunker (LLM boundaries,
  deterministic validation), at far lower cost.
- **Scope:** this is a structured-contract optimization. It does not replace the agentic RLM for documents
  without clear structural items, where the interpreter's coverage reasoning earns its cost.
