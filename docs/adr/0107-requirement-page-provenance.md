# ADR-0107: page provenance on a Requirement (the policy side of a finding can point into its document)

**Status:** accepted · **Date:** 2026-09-14 · **Resolves:** engine issue 0043 · **Related:** ADR/issue 0032 (span page provenance — the contract side), FR-Q.6 (cited provenance)

## Context

A `ComplianceFinding` cites both sides — `citation_claim` (the customer document) and `citation_requirement` (the policy). A **contract span** carries `pages` + best-effort `bbox` (issue 0032), so a product opens the document at the right page and draws a rectangle. A **`Requirement`** carried only `citation` (the human-readable section) and **no pages/bbox**, so a finding could open the policy but not point into it. Policies are disproportionately scans, where a page is nearly always knowable from the parse (0/3 bboxes but 3/3 pages on RuleWright's OCR'd sample) but a text search fails silently — so page provenance from the parse (not text matching) is the right mechanism, exactly as 0032 established.

## Decision

**Give `Requirement` the same `pages`/`bbox` provenance a span has, sourced from the policy section it was extracted from.**

- `Requirement` gains `pages: list[int] = []` and `bbox: tuple[float, float, float, float] | None = None` — additive; `citation` stays the always-present human-readable provenance.
- A requirement is bound to a policy **section**, so its provenance is that section's. `document_to_sections` now records, per section, the source `pages` its items span (from each item's `prov[0].page_no` — present on scans) and a **best-effort `bbox`**: a single-item section reports that item's box; a multi-item/multi-page section reports `None` (a section is not one rectangle, and a fabricated box is worse than none — "we'd rather show nothing than guess").
- The `DocumentRegulationAdapter` carries the section's `pages`/`bbox` in the `SourceDocument` metadata; `run_requirement_extraction` threads them through the extraction graph state; the adapt node stamps them onto every `Requirement` via `model_copy` (pages/bbox are not part of `requirement_id`, so identity and idempotency are unaffected). `to_requirements` is unchanged.
- The store persists `pages` (ARRAY_OF_INTEGERS) + `bbox` (best-effort `[l,t,r,b]` JSON string) on the `Requirement` node, exactly like a span record; `all_requirements` selects them and `_requirement_from_row` parses them back.
- The pre-sectioned eCFR path (`RegulationAdapter`, `sections.json`) has no parse, so its requirements have `pages=[]` — honestly absent, not fabricated.

## Consequences

- A finding now cites the policy page: the same product code path that opens a contract at a span's page and marks it opens the policy at the requirement's page and marks it (or, on a scan / multi-item section, opens the page with no rectangle — the honest fallback the contract side already uses).
- Fully additive and back-compatible: `pages=[]` / `bbox=None` by default; the generic and eCFR paths are unchanged. **No store migration needed** — ArcadeDB is schema-flexible, so the write adds the two properties even on a `Requirement` type created before this change, and it round-trips (verified live); a row written before this change reads back as `pages=[]` (honestly absent).
- Granularity is section-level (the requirement's binding). RuleWright's verification — a requirement's `pages` land on the page whose rendered text contains its `requirement_text` — holds: the requirement's page is among its section's pages. A finer per-requirement page would require locating the extracted text, which is unreliable and text-search-based (rejected, per issue 0043).

Full suite: 1582 passed, 44 skipped. Live-verified: a Requirement with `pages=[7,8]`/`bbox` and one with `pages=[3]`/`bbox=None` round-trip through ArcadeDB, including on an un-migrated type.
