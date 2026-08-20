# ADR-0061: Check a subject DOCUMENT for compliance, segmented per section

Status: Accepted (2026-08-20)
Date: 2026-08-20
Component: the compliance-check subgraph (`subgraphs/compliance_check.py` — `run_compliance_document_verdict`,
`document_facts_fn`, the `facts_fn` seam on `production_generic_compliance_check`) and the compliance MCP tools
(`mcp/compliance_server.py` — `check_compliance_document`, `production_document_check_fn`, `DocumentCheckFn`).
Raised by: RuleWright (product), engine issue 0008.
Related: ADR-0060 (scope a check to named policy sources — `sources` is threaded here too), ADR-0050 (compliance
ingest — the policy-document path this mirrors), ADR-0057 (async engine), ADR-0052 (engine/product split).

## Context

Engine issue 0008. Every compliance entrypoint accepted the subject as free text (`subject_text: str`), and the
generic path wrapped the WHOLE subject as ONE `CheckableFact` (`generic_facts_fn`). So the product could not
upload a subject DOCUMENT to check, and even after converting a document to text, a multi-page subject was
judged as a single coarse blob — one lumped verdict rather than per-section findings.

The policy side already solved the hard parts: `run_compliance_document_ingestion` parses uploaded bytes with
`parse_document_bytes` (docling; PDF/DOCX/MD/TXT) and splits at headings with `document_to_sections`. The
subject side needed the same treatment, mirrored.

## Decision

Add `run_compliance_document_verdict(doc_name, data: bytes, *, store, judge_model_id, embedder, k=8,
sources=None, sections_fn=None)`: parse the uploaded subject, split it at its headings (`document_to_sections`),
turn each section into a `CheckableFact` (`document_facts_fn`), and judge each section against the Requirement KG
→ a `ComplianceReport` with **per-section cited findings**. This solves upload AND subject segmentation in one,
reusing the exact seams the policy-document ingest uses.

- A small `facts_fn` seam is added to `production_generic_compliance_check` (default `generic_facts_fn`, i.e.
  whole-subject one fact); the document path injects the per-section producer.
- The subject is **transient** — parsed and checked, never written to the store (unlike a policy, which is
  ingested). A document with no headings degrades to one whole-doc section (graceful).
- `sources` (ADR-0060) is threaded through, so an uploaded subject can be scoped to named policies; an unknown
  name still raises `UnknownComplianceSourceError`.
- **Every surface gets it** (no half-fix): the MCP tool `check_compliance_document` takes base64 `data` (JSON
  cannot carry raw bytes), decodes and forwards to a `DocumentCheckFn`; `production_document_check_fn` wires the
  real path; and `main()` now serves all three compliance tools (this also closed a pre-existing gap where the
  generic/document tools were never wired into the stdio entry).

## Consequences

- **Verified live** (real docling parse + BGE + Granite judge vs the live FTC 16 CFR 255 KG): a 5-section
  marketing document → per-section cited findings, with violations correctly attributed to the section that
  contains the problem (Clinical Results → §255.1/§255.2 unsubstantiated claims; Customer Testimonials → §255.5
  undisclosed paid endorsement). Confirms parse → heading-split → per-section verdict works end-to-end on a real
  document, not just hermetically.
- **Known characteristic (future refinement, not a blocker):** findings are the per-section × per-selected-
  requirement cross-product, so a multi-section document produces many findings (44 on the 5-section test —
  mostly `compliant`/`needs_review` filler alongside the real violations). The `gap_matrix` and verdict rollup
  absorb it; a future UX refinement is a per-section rolled-up verdict rather than every pair. It is the same
  shape as the text path, multiplied by sections.
- **No `ComplianceReport` shape change** — it already carries multiple findings + gap_matrix; the report's
  coverage reflects the per-section structure automatically.
- New public surface: `run_compliance_document_verdict`, `document_facts_fn`, and the MCP
  `check_compliance_document` tool / `DocumentCheckFn` / `production_document_check_fn`.
- Segmentation granularity is per-heading-section today; finer paragraph/sentence splitting for tighter
  citations is a documented later refinement.
