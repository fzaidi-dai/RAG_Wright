# ADR-0070: Text-layer-first parsing — a born-digital page is authoritative and is never OCR-escalated

Date: 2026-09-04
Status: Accepted (implemented; PARSE-1, bulk-ingestion wall reported by RuleWright)

Refines the tiered OCR parser (0009-WIRE / `TieredOCRParser`): the scan-quality gate must not second-guess a page
that already carries a usable native text layer.

## Context

RuleWright's bulk CUAD ingest ran ~4 minutes per contract. First-hand reproduction on
`NEONSYSTEMSINC_…DISTRIBUTOR AGREEMENT_Amendment.pdf` showed the "4-minute OCR" was **neither OCR nor the model**:
the PDF is **born-digital** (all 5 pages have a text layer; pypdfium2 extracts 18,479 chars directly, no OCR).
But the scan-quality gate assessed **page 5** — the signature/joinder page (sparse prose: names, titles,
"SKUNKWARE, INC.", a page number) — as `DEGRADED` on OCR **word-hit-rate**, and `TieredOCRParser` escalated the
**whole document** to the Gemma-4 VLM over OpenRouter (~30s+/page). The gate was overriding an authoritative text
layer with an OCR-quality heuristic that legitimately reads low on a sparse-but-correct born-digital page.

Measured: docling's own parse is ~2–6s; the ~4 minutes was entirely the false-positive VLM escalation. A
born-digital contract (most of CUAD, and most real customer contracts) paid it on every ingest.

## Decision

A page with a usable **native text layer** is authoritative — never OCR-quality-assess or VLM-escalate it.

- `_text_layer_pages(source)` reads each PDF page's directly-extractable text via pypdfium2 (no OCR) and returns
  the 1-based pages with `>= _MIN_TEXT_LAYER_CHARS` (200) of text — born-digital pages. Best-effort: a non-PDF or
  any read error → empty set (no override), so text/office sources and the hermetic tests are unaffected.
- In `TieredOCRParser.convert`, the `degraded` set (pages the OCR/image gate flagged) is filtered to pages
  **without** a text layer. Per-page, so a genuinely image-only page inside an otherwise born-digital PDF still
  escalates. When every "degraded" page turns out to be born-digital, the fast doc is returned with no VLM cost.

The threshold is conservative and one-directional: an image-only scanned page extracts ~0 chars (never
misclassified as born-digital), while a sparse born-digital page still extracts hundreds (NEONSYSTEMS p5 = 1428).
The rare "searchable scan with an embedded garbage OCR text layer" is a documented limitation — its embedded text
is what any extractor would use anyway.

## Consequences

- **Fixed, live-verified.** NEONSYSTEMS parse **4 min → 6.7s** with the OpenRouter key set (the exact repro
  condition): all 5 pages recognized as born-digital, `escalated=[]`, `unreadable=[]`. Zero content risk — the
  text layer was always the true source; we stopped second-guessing it.
- **Scan path intact, live-verified.** A real image-only scan (`PcquoteCom…Agreement2`, 0 text-layer chars):
  `_text_layer_pages=∅`, the tiered OCR→VLM path ran unchanged (`escalated=[1,2]`, `unreadable=[2]` flagged
  PARTIAL), OCR produced real text. PARSE-1 only ever removes a born-digital page from the escalation set.
- **Bulk speed.** Every born-digital contract stops paying the false-positive VLM escalation — the dominant slice
  of RuleWright's per-contract wall-clock.
- **No API/identifier/schema change.** `_text_layer_pages` is an internal helper; `TieredOCRReport` is unchanged;
  the parse cache/gate is unchanged. Hermetic tests inject `_text_layer_pages` via monkeypatch.
- This is the first of the bulk-ingestion-wall tasks; DEFRAG-1 (stop shattering clauses on line-wrap `\n\n`) and
  EXTRACT-GUARD-1 (graceful-degrade on furniture/empty extraction) follow, then the TAGPARSE-INGEST-1 backlog.
