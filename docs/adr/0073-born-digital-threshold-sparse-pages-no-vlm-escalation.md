# ADR-0073: Lower the born-digital text-layer threshold so a sparse real page is not VLM-escalated

Date: 2026-09-04
Status: Accepted (implemented; PARSE-2, bulk-ingestion wall reported by RuleWright)

Refines ADR-0070 (text-layer-first parsing): the threshold that decides "this page has an authoritative text
layer" was set too high, so sparse-but-real born-digital pages were treated as scans.

## Context

RuleWright's wider sample surfaced a new failure mode: a large born-digital contract
(`AimmuneTherapeuticsInc_…Development Agreement`, 63 pages / 168K chars) came back **`failed` at exactly 600.0s**
— a round number that is a deadline, not work completing. Diagnosis (first-hand):

- All 63 pages are born-digital, but **pages 52–58 carry only 91–179 chars** (a schedule / signature / exhibit
  section). ADR-0070's `_MIN_TEXT_LAYER_CHARS = 200` classified those as NOT born-digital.
- The scan-quality gate flags degraded pages `[1, 2, 24, 51, 52–58, 61]` (it routinely over-flags sparse
  born-digital pages on OCR word-hit-rate — the same false-positive as NEONSYSTEMS page 5). The ≥200 pages are
  protected by ADR-0070, but pages 52–58 leak through.
- Any page left in `degraded` triggers a **whole-document** VLM escalation (`TieredOCRParser` re-parses the whole
  PDF via Gemma-4 over OpenRouter). On a 63-page doc that is ~30 min of VLM work → it blows the 600s parse
  deadline (`_INGEST_PARSE_DEADLINE_S`) → dead-lettered as `failed`.

The docling parse itself is ~20s (with or without OCR), so OCR was never the cost — the cost was an unnecessary
VLM escalation caused by mislabeling sparse-but-real pages as scans.

## Decision

Lower `_MIN_TEXT_LAYER_CHARS` from **200 to 30**. A page with any real native text layer (even tens of chars) is
authoritative and is never OCR-quality-assessed or VLM-escalated; only a genuinely image-only page (which extracts
~0 chars) is. The threshold separates "has a text layer" from "is an image", not "is dense" from "is sparse".

Proven: on doc 3, born-digital detection goes 56/63 → **63/63**; `_text_layer_pages@30` filters every gate-flagged
page out of the escalation set, so there is **no VLM escalation**.

## Consequences

- **Fixed, live-verified end-to-end.** doc 3: parse **600s (deadline, FAILED) → 32.7s** (`escalated=[]`), and the
  full ingest now completes **INGESTED (clean)** — 230 clauses, 700 spans, **0 failures**, 461s total (under the
  600s ceiling).
- **Scan path intact.** A true image-only page still extracts ~0 chars (< 30) → not born-digital → still routed
  through OCR/VLM (the hermetic tests cover a `9`-char page → not born-digital, a ~100-char page → born-digital,
  and the existing scan-escalation tests are unchanged).
- **Bounded new risk.** A genuinely scanned page carrying a small (30–90 char) embedded OCR text layer would now
  be trusted rather than re-VLM'd. This is rare, and the embedded text is what any extractor would use anyway; the
  alternative (mislabeling every sparse real page as a scan) fails whole documents.
- **Residual, tracked separately (PARSE-3).** A *large* doc containing a genuine image-only page (0 chars) still
  triggers a whole-document VLM escalation for that one page → deadline. The right fix is per-page VLM or a
  page-count cap on escalation, not the threshold. It does not affect doc 3 (all pages born-digital).
- **No API/identifier/schema change** — a single tuning constant plus its rationale.
