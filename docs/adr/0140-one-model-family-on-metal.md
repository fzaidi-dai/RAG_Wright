# ADR-0140: One model family on Metal, under one process lock

**Status:** accepted · **Date:** 2026-10-29 · **Related:** PS-55, TexWright's request
`2026-10-25-two-model-families-in-one-process-abort-on-metal.md`, RuleWright T-7.25 / T-7.46 (`_avoid_mps`), ADR-0076
(issue 0016, the per-instance embedder lock), ADR-0138 (pdfium)

## Context

PyTorch's Apple Metal (`mps`) backend is not safe for concurrent use. Two threads running models on it abort the
process inside Metal (`failed assertion _status < MTLCommandBufferStatusCommitted ... setCurrentCommandEncoder:`) or
segfault it. Either way it is a native `abort()`, so nothing in Python can catch it.

The engine reached Metal from three model families, each choosing its own device automatically:

- the BGE-M3 embedder (`_resolve_device`);
- the BGE reranker (FlagEmbedding's choice);
- Docling's layout, table and RapidOCR-torch models (`AcceleratorOptions(device="auto")`).

It also runs them on different threads: an ingest embeds one document while it parses another, and a server embeds
a query while an upload parses. Issue 0016's per-instance locks do not help across instances or families.

Reproduced with the BGE-M3 embedder in one thread and Docling in another, each run in a child process:

- On torch 2.14.1 (TexWright's environment), 6 of 6 runs died within seconds: 5 SIGABRT with TexWright's
  assertion, 1 SIGSEGV. Three of the runs OCR'd a screen image and three parsed PDFs.
- On torch 2.12.1 the same shapes survived every run, so the failure depends on the torch version, and the newer
  version makes it certain.
- RuleWright measured it independently on their contract ingest: 4 crashes in about 8 runs on Metal, 0 in 6 on CPU.
  They fixed it in their own seam (`_avoid_mps`), and the engine never adopted the fix.

`docs/workspaces.md` claimed that concurrent calls in one process are safe without qualification.

## Decision

- **`rag_wright.util.devices`** owns the engine's device policy. Unless told otherwise, it uses CUDA when present.
  On Metal it allows only the BGE family (the embedder and the reranker, the hot path, about 2x faster there).
  Everything else goes to CPU.
- **Docling** runs on CPU where its `auto` would pick Metal. The engine passes the device explicitly, because
  Docling's default options are built once at import. Docling's output is identical on CPU and Metal (measured on
  the engine's fixtures).
- **The reference pack's classifiers** (SetFit, Laya) follow the same policy. Packs get it through
  `pack_sdk.torch_device`, which never picks Metal unless asked.
- **On Metal, every BGE call holds `METAL_LOCK`**, one re-entrant lock per process. Two embedders, or an embedder
  and the reranker, never run on Metal at once. Off Metal each instance keeps its own lock (issue 0016), so a CUDA
  host loses no concurrency.
- **Explicit choices always win**, `mps` included: `EMBED_DEVICE` (the whole BGE family), `DOCLING_DEVICE`,
  `RAG_SETFIT_DEVICE`, and any `device=` argument. This keeps the crash reproducible, and it means an operator who
  puts a second family on Metal gives up the guarantee.

## Consequences

- A product needs no process-per-pipeline workaround. A server may stage an upload's ingest beside query traffic.
  RuleWright can delete `_avoid_mps`, and TexWright can delete its spawned-process seed and the skipped browser test.
- On Apple Silicon, Docling parses and the pack classifiers run on CPU (slower than Metal), and BGE calls from
  different instances queue behind one another. A CUDA or CPU-only host is unchanged.
- The rule holds only while engine code chooses the devices. A product's own torch model should take its device from
  `pack_sdk.torch_device`, or it must stay off Metal.
