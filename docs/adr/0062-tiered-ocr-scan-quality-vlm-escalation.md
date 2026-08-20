# ADR-0062: Tiered OCR — fast engine, scan-quality gate, VLM escalation, PARTIAL fallback

Status: Accepted (2026-08-20)
Date: 2026-08-20
Component: the parsing seam (`capabilities/parsing.py` — `TieredOCRParser`), the scan-quality gate
(`capabilities/scan_quality.py`), the VLM-OCR capability (`capabilities/vlm_ocr.py` + `ModelRole.VISION_OCR`),
the document-bytes chokepoint (`corpus/document_parser.py` — `parse_document_bytes` / `aparse_document_bytes`),
and the ingest PARTIAL surface (`subgraphs/contract_ingestion_pipeline.py`, `subgraphs/async_ingestion.py`).
Raised by: the "RapidOCR is ~60s/page, too slow" report. Benchmark: `docs/eval/ocr_benchmark.md`,
`scripts/ocr_benchmark.py`.
Related: ADR-0057 (async wall-clock deadline — the escalation must be bounded), ADR-0039 (self-hosted Granite
substrate — VISION_OCR is a documented exception), ADR-0006 (model-profile seam — VISION_OCR resolves through
it), ENG-1/ENG-2 (the unified `failures` contract — `ocr` is a new kind).

## Context

The reported symptom was OCR speed (RapidOCR "~60s/page"). A benchmark over docling's OCR engines and VLM
pipelines (`ocr_benchmark.py`) against ground truth reframed the problem:

1. **Speed is not the issue.** On macOS every engine is 0.8–4s/page on a real 150 DPI scan; the default already
   auto-selects Apple Vision (~1s). The "60s" was most likely the whole 5-page document or a Linux RapidOCR-CPU
   path — not per-page.
2. **Accuracy is not engine-differentiated on readable scans.** On the RuleWright clean/moderate contract
   fixtures (exact ground truth) every engine scores ~1.000. Swapping the OCR engine buys nothing there.
3. **On a heavily degraded scan, everything fails — including the VLM pipeline.** Traditional OCR emits garbage
   (char_sim 0.006–0.10); Granite-Docling-258M also fails (0.023) and slows 8× (35s/page). Diagnosis: severe
   blur (Laplacian variance 54 vs clean's 2440) + faded ink (dark_frac 0.003). Blur destroys glyph *shapes*,
   which is what character-based OCR matches; classical preprocessing (deskew/denoise/Sauvola/CLAHE) cannot
   un-blur smeared glyphs (measured: heavy stayed ~0.01–0.04 with preprocessing).
4. **But the heavy text is human-readable** — so a strong VLM reads it by language context. Via docling
   `ApiVlmOptions` → **OpenRouter** (the provider we already use for Gemma-4; no Modal, no local model),
   **Gemma-4 char_sim 0.991** and Qwen-2.5-VL 0.977 on the heavy scan — the only things that read it, at ~32s/page.

So OCR-engine choice is a speed/deployment decision; the accuracy lever for degraded scans is *around* OCR.

## Decision

A **tiered OCR** parser (`TieredOCRParser`, a `Parser` that drops into the existing `parse(..., parser=)` seam):

1. **Fast OCR by default** (docling default; Apple Vision on macOS, ~1s/page) — perfect on readable scans, zero
   VLM cost in the common case.
2. **Scan-quality gate** (`scan_quality.assess_document`) scores each page READABLE / DEGRADED / UNREADABLE from
   a common-word hit rate (garbage OCR ~0), image metrics (Laplacian variance, dark_frac), and docling's own
   `PageConfidenceScores`. Thresholds validated in the benchmark.
3. **VLM escalation** for degraded pages (`vlm_ocr` → docling VLM pipeline → OpenRouter). Whole-document
   escalation (the VLM reads good pages fine too, so it is safe and keeps per-page splicing out of the MVP). The
   model is `ModelRole.VISION_OCR`, **default Gemma-4, swappable via `RAG_MODEL_VISION_OCR`**.
4. **PARTIAL fallback**: a page still unreadable after the VLM is genuine information loss → surfaced, never
   silently ingested.

**Wired at the chokepoint, default-on, async-bounded, graceful-degrade:**

- `parse_document_bytes` defaults to `TieredOCRParser`, so contract ingestion, both compliance paths, and the
  MCP `check_compliance_document` tool all get it in one place. **Default-on** because ingestion already makes
  OpenRouter calls (Granite-8B extraction etc.), so gating it opt-in buys nothing.
- **Graceful degrade**: no `OPENROUTER_API_KEY` (or a VLM error) → skip escalation, flag PARTIAL, keep the fast
  doc, never crash — so default-on is safe.
- **Async-bounded** (ADR-0057): `aparse_document_bytes` runs the sync tiered parse (incl. the ~35s/page VLM, the
  slowest call in the pipeline) off the event loop via `to_thread` under an `asyncio.timeout`, wired into
  `run_compliance_document_verdict` (the live async/MCP path that previously parsed on the loop).
- **Structured PARTIAL**: `ocr` is a **new loss kind** in the unified `failures` list (ENG-1/ENG-2). The tiered
  parser's `unreadable_pages` ride on `SourceDocument.ocr_unreadable_pages` (captured at parse, cached in an
  `.ocr.json` sidecar so resume preserves it) and both ingest drivers fold them into `IngestionReport.partial`
  via `build_partial_entry(ocr_failures=...)`.

**VISION_OCR is a documented exception to ADR-0039's "every role → Granite."** OCR needs a vision model; the
Granite-8B product LLM is text-only. The default (`google/gemma-4-31b-it`) is provider-agnostic — OpenRouter
now, a self-hosted vLLM-Gemma-4 later for data sovereignty (only `base_url` changes).

## Consequences

- **Readable scans are unchanged and free**: the gate finds them readable, no VLM call, identical output. Only
  genuinely degraded pages pay the VLM cost.
- **Degraded-but-readable scans are now recoverable** (Gemma-4 0.991) instead of ingested as garbage.
- **Genuinely unreadable scans surface as an `ocr` PARTIAL**, not silent gibberish — and, per the forward-compat
  contract, an integrator counting the kind-tagged `failures` list picks up the new kind with no code change.
- **New public surface**: `TieredOCRParser` / `TieredOCRReport`, `scan_quality.{assess_scan, assess_document}`,
  `vlm_ocr.{openrouter_vlm_options, build_vlm_ocr_converter, vlm_ocr, VlmOCRParser}`, `ModelRole.VISION_OCR`,
  `aparse_document_bytes`, and `build_partial_entry`'s optional `ocr_failures` arg.
- **Benchmark-only deps** (`ocrmac`, `mlx-vlm`, `onnxruntime`, `scikit-image`) live in an isolated `ocr-bench`
  extra; the tiered path itself needs only docling + `cv2` (already present).
- **Known follow-ups** (deliberately deferred, not silently dropped): (1) **per-page** escalation instead of
  whole-document, to cut VLM cost on a mostly-readable doc with a few bad pages; (2) **self-hosted vLLM-Gemma-4**
  as the escalation provider for data sovereignty; (3) routing the vision call **through the async model seam**
  for true thread cancellation (today `to_thread` unblocks the caller at the deadline but the docling thread
  runs on); (4) classical preprocessing stays available as a cheap first pass but is not the heavy-scan fix.
