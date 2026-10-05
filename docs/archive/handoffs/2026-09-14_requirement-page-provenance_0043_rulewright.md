# RuleWright handoff: page provenance on a Requirement (issue 0043) — the finding can now point into the policy

Date: 2026-09-14 · on `origin/main` · ADR-0107 · **Additive. `Requirement` gains `pages` + best-effort `bbox`, same shape as span provenance (0032). No store migration.**

---

## What you get

`Requirement` now carries the **same page provenance a span does** (0032), so the same product code path serves both sides of a finding:

```python
req.pages   # list[int] -- the policy section's source page(s); [] when unknown (honestly absent, never fabricated)
req.bbox    # (l, t, r, b) | None -- best-effort; None for a multi-item section or a scan with no text layer
req.citation  # unchanged: the always-present human-readable "§ 10.5"
```

A finding cites the requirement, so `finding` → its `Requirement` → `pages`/`bbox`. Open the policy at `pages[0]`, draw the rectangle if `bbox` is present, else just open the page — exactly as the contract side behaves for the 32/190 spans with no rectangle.

## How it's sourced (why it works on scans)

A requirement is bound to a policy **section**, so its provenance is the section's, taken from the parse's per-item page info (`prov[0].page_no`) — **present on scans**, where a text search would fail silently. `document_to_sections` records each section's `pages` (the pages its items span, so a section crossing a page boundary reports both) and a best-effort `bbox` (a single-item section → that item's box; a multi-item section → `None`, because a section is not one rectangle). We do **not** locate the requirement by searching the rendered PDF — the failure mode you rejected.

Granularity is section-level. Your verification holds: a requirement's `pages` land on the page whose rendered text contains its `requirement_text` (that page is among its section's pages).

## Scope / edges

- **Scans**: page reported, `bbox` absent (`None`) — never a fabricated rectangle. This is the common policy case.
- **Pre-sectioned eCFR corpus** (`sections.json`, no parse): `pages=[]` — honestly absent. Page provenance needs a parsed source document (`run_compliance_document_ingestion`), which is your customer-policy path.
- **No store migration**: ArcadeDB is schema-flexible, so ingesting into an existing compliance DB adds the `pages`/`bbox` columns and round-trips (verified live). A requirement written before this change reads back as `pages=[]` — so to backfill provenance, re-ingest the policy document; new ingests carry it automatically.

## Verify it your way

Ingest a policy document, take a `Requirement` off a finding, and assert its `pages` land on the page whose rendered text contains its `requirement_text`; and the scan case: a scanned policy still reports pages with `bbox` absent. Engine-side: a Requirement with `pages=[7,8]`/`bbox` and one with `pages=[3]`/`bbox=None` round-trip through ArcadeDB (including on an un-migrated type).

Reference: ADR-0107, `contracts/compliance.py::Requirement` (`pages`/`bbox`), `corpus/document_parser.py::document_to_sections`, `subgraphs/compliance_ingestion.py` + `subgraphs/requirement_extraction.py` (thread the section's pages), `store/arcadedb.py` (persist + read), `subgraphs/compliance_check.py::_requirement_from_row`, engine issue `docs/engine-issues/0043-...`. Full suite: 1582 passed.
