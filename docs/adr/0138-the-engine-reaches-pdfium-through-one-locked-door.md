# ADR-0138: PDF parses run one at a time, and the engine reaches pdfium through one locked door

**Status:** accepted · **Date:** 2026-10-12 · **Related:** RuleWright engine issue 0052, PS-29, ADR-0124
(ING-6 embedded files), PARSE-1/3 (the text-layer probe and page renders)

## Context

pdfium is not thread-safe, and the engine parses several documents at once (`IngestionTuning.document_concurrency`;
RuleWright runs 4). Reproduced here on 15 CUAD PDFs, 4 in flight, fresh cache, each run in a child process: 3 of 3
runs crashed in libpdfium (SIGTRAP in page-object destructors on a worker thread; SIGABRT in
`CPDF_ColorSpace::DestroyGlobals` at exit on a corrupted heap). Two causes:

1. The engine's own pdfium calls (page count, text-layer probe, page renders; PDF attachments and their text) took no
   lock while docling parsed another document, and three never closed their documents. Fixing only this left the
   crash: 3 of 3 runs still failed, and a faulthandler dump showed the fault surfacing in a correctly locked close
   while no other thread was in pdfium -- the heap had been corrupted earlier.
2. pdfium objects created during a docling conversion can be freed later by the garbage collector, on any thread,
   outside any lock (pypdfium2 frees them through weakref finalizers). docling locks its own calls, not that.

## Decision

- `rag_wright.util.pdfium.pdf_work()`: one process-wide re-entrant lock around each PDF's parse (docling's conversion
  plus the engine's probes), ending with a garbage collection inside the lock, so nothing a parse created is freed
  beside another document's parse. `parse_document_bytes` takes it for a PDF (by extension or `%PDF-` bytes); other
  formats, and every later ingestion stage, stay concurrent. Images do not use pdfium (docling's image backend
  bypasses it).
- `open_pdf(source)`: the engine's only way to pypdfium2; it takes `pdf_work()`'s lock and then docling's
  `pypdfium2_lock` (always in that order) and closes the document before releasing them.
  `tests/arch/test_pdfium_door.py` fails on any other import of pypdfium2 and checks that PDF parses never overlap.

## Consequences

- Measured on the same 15 PDFs: 0 of 5 runs crashed; the parse took 124-129 s (mean 127) against 143 s for the
  unlocked concurrent runs that got that far and 130 s serial. On this machine (Apple Silicon, CPU) concurrent PDF
  parses never helped: docling's CPU-bound models contend. A GPU-backed host may differ.
- A scanned PDF that escalates to the vision model holds the lock during those calls; other PDFs wait (logged when
  a wait exceeds 10 s).
- RuleWright can ingest PDFs concurrently again. Running parses in separate processes (no shared pdfium) is the way
  to regain parse concurrency if a host needs it; it is a separate design question.
