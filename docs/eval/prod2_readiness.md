# PROD-2 readiness note — compliance-KG ingestion (2026-08-11)

Objective (ADR-0049 generic-customer lens): does the COMPLIANCE side — a different KG (`ragwright_compliance`),
ontology (`compliance_bridge.ttl`, deontic Requirement = obligation/prohibition/permission), and pipeline —
generalize to ANY customer regulation, not just the tuned FTC 16 CFR 255? Two phases; Phase 1 done.

## Phase 1 — extraction generalization (DONE, PASSED)

Isolates EXTRACTION generalization from the parsing gap: ingest a DIFFERENT eCFR part through the SAME pipeline
and inspect the Requirement KG.

**Setup.** Acquired **16 CFR 233 (Guides Against Deceptive Pricing)** — a different FTC rulebook, same advertising
domain. The eCFR section parser (`parse_sections`: DIV8/HEAD/P) is the STANDARD eCFR-XML structure, so it was
generalized into `scripts/acquire_ecfr.py` (parameterized TITLE/PART/CHAPTER/SUBCHAPTER) with ZERO part-specific
code — 16 CFR 233 parsed cleanly into 5 sections. Ingested via the existing `compliance_ingestion` subgraph
(Granite requirement extraction) into a scratch DB `ragwright_compliance_prod2`.

**Result — clean, no code changes:**
- 5/5 sections ingested, **0 dead-lettered** → **24 Requirement nodes**.
- Deontic split **14 obligation / 10 prohibition** (no permissions — correct for a duties/bans rulebook); no
  garbage deontic types.
- **No over/under-generation:** 2–8 requirements per section, tracking section length (§233.3 longest → 8;
  §233.4 shortest → 2). NOT the FTC-255 definitions-leak pattern (`skip_definitions` did its job; no definitions
  section here anyway).
- **100% applicability scope** (24/24) and clean, on-domain requirement text ("A former price... must be a bona
  fide, genuine price", "must not be fictitious") — genuinely deceptive-pricing content, correctly extracted.

**Verdict (Phase 1):** the FTC-255-tuned compliance pipeline GENERALIZES to a different rulebook with no code
change — the deontic ontology + requirement extraction are domain-generic, as the generic-customer lens predicts.
Extraction already uses `extraction_contract="auto"` (skeleton-then-fill), so the large-doc truncation that bit
the contract party path does not apply here.

## Phase 2 — customer-document ingestion (DONE, PASSED live via DOCPARSE-1)

The real customer gap: a customer's OWN policy PDF/DOCX, not a pre-sectioned eCFR `sections.json`. DOCPARSE-1
built the generic parser (`corpus/document_parser.py`) + a `DocumentRegulationAdapter` (docling parse →
heading-split sections → `SourceDocument`s, the same shape `RegulationAdapter` yields);
`run_compliance_document_ingestion` wires it into the same pipeline.

**Live result** (16 CFR 233 reconstructed as a structured policy DOCUMENT, ingested via the document path,
`scripts/ingest_compliance_document_prod2.py`): 5/5 sections, **0 dead-lettered**, **28 Requirement nodes**,
deontic 19 obligation / 9 prohibition, clean on-domain text. **Comparable to the Phase-1 `sections.json` path on
the same content** (24 requirements, 14/10) — within LLM run-to-run variance; same sections, same deontic
character. So the GENERIC DOCUMENT PARSER produces equivalent output to the structured eCFR-XML producer: a
customer's own document ingests into the compliance KG through the identical pipeline. Note (DOCPARSE-1):
heading-sectioning needs a STRUCTURED source (PDF/DOCX/MD with headings); a flat .txt yields one section.

## Findings / gaps (the compliance hardening backlog)
1. **Generic raw-doc→sections parser — RESOLVED (DOCPARSE-1 + Phase 2 above).** A customer policy PDF/DOCX now
   ingests via `DocumentRegulationAdapter` / `run_compliance_document_ingestion`, validated live.
2. **No `is_done` resume marker for compliance.** `run_compliance_ingestion` passes no `is_done`, so a re-run
   re-extracts every section (the contract side skips via a present `Contract` node). Add a per-section "already
   written" check for resumable compliance ingests.
3. **Same lossless nuance as PROD-3 Increment 1, different path.** Requirement extraction degrades a failed
   section to `None → []` (genuine-empty vs failure ambiguity), via the model seam (not docling), so the
   `capture_docling_errors` fix does not apply. A parallel lossless hardening (retry + flag, not silent-empty)
   would bring compliance to the same "no silent partial success" bar. (Not hit here — 0 dead-letters — but the
   surface exists.)
4. **Cross-DOMAIN (non-advertising) generalization not yet tested.** Phase 1 stayed in the FTC advertising family
   (same-domain, cheapest). A genuinely different domain (privacy/GDPR, financial, safety) is the stronger test —
   a follow-on when warranted.
5. **Not yet made async (PROD-3).** Compliance ingestion is still the blocking `run_corpus_ingestion`; the PROD-3
   async job envelope (`submit_ingestion`/`run_job`) is corpus-generic and would wrap it identically.

## Verdict
Compliance extraction is production-shaped and generalizes across rulebooks (Phase 1 proven). The open items —
the generic raw-doc→sections parser (Phase 2), compliance resume, lossless hardening of the requirement path,
cross-domain testing, and async — are the hardening backlog, not blockers to the pipeline itself.
