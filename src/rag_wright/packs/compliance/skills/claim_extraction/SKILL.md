---
name: claim_extraction
description: >
  The subject-document claim-extraction method: read an advertisement and pull out its distinct CHECKABLE
  claims (the assertions a regulator could test), each with its kind, the disclosures present near it, and
  whether the ad references evidence. Applied by the compliance_check subgraph (subject side). The schema is
  the co-located asset `template.py` (ExtractedAd / ExtractedClaim); the deterministic mapping to the closed
  Claim vocab is the claim_adaptation FUNCTION's job, not this skill's.
---

# Claim extraction: what checkable claims does this ad make?

This skill teaches a **method**, not a behavior. It turns a subject advertisement into the checkable claims the
compliance check will judge. It is authored software (an Agent Skill), with its extraction **schema** as the
co-located asset **`template.py`** (`ExtractedAd` → `ExtractedClaim[]`), referenced here and filled by the model.

## What to extract (the schema — `template.py`)

Per ad, produce an `ExtractedAd` whose `claims` are the distinct **checkable** assertions. For each claim:

- **assertion_text** — the claim itself, quoted or closely paraphrased; one claim per entry.
- **claim_type** — the kind, from the closed vocab: `efficacy, comparative, pricing, health, environmental,
  endorsement, performance, guarantee`.
- **disclosures_present** — any disclaimers/qualifiers near the claim: `#ad`, `paid partnership`, `results vary`.
- **evidence_referenced** — whether the ad points to a study/data for the claim.
- **actor / subject_product / quantitative_value / medium** — when present.

Extract the **checkable** assertions (a regulator could test them), not pure subjective flourish — but when in
doubt, extract it; the judgment step decides puffery vs objective claim.

## The reliability method (docling-graph, from GP-1B)

The extractor runs through docling-graph in API mode. Three settings are load-bearing and must not drift:

1. **source must be a file path**, not a raw string — docling-graph `stat()`s it (write the text to a temp file).
2. **`structured_output=False`** (json_object) — the strict nested json_schema returns nothing on some models;
   json_object is reliable across DeepSeek / Gemma / Granite.
3. **a `max_tokens` cap** — an unknown provider else gets a generic 8192 context window and SKIPS the LLM.

Ads are short, so `extraction_contract="direct"` (one call) is correct here.

## What this skill does NOT own (the applying capability's job)

- mapping `claim_type` to the closed `ClaimType` vocab and coercing an off-vocab value (kept-but-AMBIGUOUS, a
  checkable assertion is never dropped) — the `claim_adaptation` FUNCTION;
- the `claim_id` content-hash identity and the span provenance — the FUNCTION;
- concurrency, timeouts, and the compliance judgment that follows — the subgraph.
