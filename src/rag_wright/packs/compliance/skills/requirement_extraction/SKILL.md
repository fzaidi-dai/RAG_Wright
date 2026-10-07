---
name: requirement_extraction
description: >
  The regulatory-rule extraction method: read one § section of a regulation and pull out the distinct DEONTIC
  rules it states (each an obligation / prohibition / permission), with the actor it binds, the claim types it
  applies to, and any evidence standard. Used by the requirement_extraction SUBGRAPH's extraction node. Because
  a section spreads its rules across the whole text, extraction runs multi-call (skeleton-then-fill) -- which is
  why the capability is a subgraph, not a single-shot skill. The schema is the co-located asset template.py; the
  deterministic mapping to the closed Requirement vocab is the requirement_adaptation FUNCTION's job.
---

# Requirement extraction: what deontic rules does this regulatory section state?

This skill teaches a **method**, not a behavior. It turns one regulatory § section into the deontic rules the
compliance check will judge against. The extraction **schema** is the co-located asset **`template.py`**
(`ExtractedRegulationSection` → `ExtractedRequirement[]`), filled by the model.

## What to extract (the schema — `template.py`)

Per section, an `ExtractedRegulationSection` whose `requirements` are the distinct **operative** rules. For each:

- **requirement_text** — one rule, paraphrased in a sentence: what must, must not, or may be done.
- **deontic_type** — `obligation` (must/required), `prohibition` (must not/may not), or `permission` (may).
- **actor** — who the rule binds (advertiser, endorser, expert, …).
- **claim_types** — which advertising claim types it applies to (from the closed vocab), when the rule is
  claim-type-specific; leave empty when it applies by context (the applicability map handles that downstream).
- **evidence_standard** — the substantiation the rule requires, if any.

Extract only **operative rules**, not the section's definitions or purpose statements.

## The reliability method (docling-graph, from GP-1B + EXTRACT-TUNE)

1. **source must be a file path**, not a raw string (docling-graph `stat()`s it — write a temp file).
2. **`structured_output=False`** (json_object) — the strict nested json_schema returns nothing on some models.
3. **a `max_tokens` cap** + a wide `preamble_chars` (a full section, not a contract preamble).
4. **`extraction_contract="auto"`** -- CRITICAL. A regulatory section spreads its rules across the whole text, so
   a single `"direct"` call SILENTLY self-rations and loses most of them (measured: §255.5 → 6 direct vs 31
   dense). `"auto"` picks dense (skeleton-then-fill, multiple calls) on long sections. This multi-call is the
   reason the capability is a **subgraph**.

## What this skill does NOT own (the subgraph / the function)

- the deterministic mapping to the closed vocab (`deontic_type` coercion -- off-vocab → AMBIGUOUS; off-vocab
  claim_type dropped; blank rule skipped), the `citation` (= the section) and the content-hash `requirement_id`
  -- the `requirement_adaptation` FUNCTION;
- the extract → adapt chaining, retry/dead-letter, and the write -- the `requirement_extraction` SUBGRAPH and the
  `compliance_ingestion` corpus driver.
