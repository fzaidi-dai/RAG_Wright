# ADR-0096: keyword-fallback normalization for value-bearing list dimensions (carve_out et al.)

**Status:** accepted · **Date:** 2026-09-10 · **Issue:** engine 0033 (RuleWright) · **Related:** ADR-0028 (lexical grounding gate), ADR-0045 (client-side tag-parse), "normalize at the boundary" · **Touches:** `ontology/clause_template.py::_normalize_enum`

## Context

`carve_out` (values incl. `indemnification`) was extractable from a query but not from the contract that states it. A liability cap that carves indemnification out of itself ("uncapped indemnity") is one of PR-16's five high-stakes conditions, and `carve_out` is the only positive form the condition has ("uncapped" is an absence, and `cap_basis` has no `none`).

Live reproduction showed the model was **not** the problem — it *did* extract the carve-out. The ingest path (tag-parse, ADR-0081) produced `excepts = ["Except in respect of the Supplier's indemnification obligations under clause 8"]` — the clause quoted **verbatim** rather than the bare token. `_normalize_enum` matched only on an exact alphanumeric-collapsed equality, so the phrase (which *contains* "indemnification") fell to `OTHER` and was dropped. The query side worked only because a short query ("...excludes indemnification from the cap") yields the clean token. So the same `(dimension, value)` was recoverable from a query and lost from the document — a normalization gap, not a recall gap, and not a mapping gap (`excepts → carve_out` was already correct).

## Decision

Add an opt-in **keyword-substring fallback** to `_normalize_enum`, enabled on the three value-bearing LIST dimensions.

- After exact match fails and before falling back to `OTHER`, if `keyword_fallback=True`, map the input to the member whose canonical **value token** (alphanumeric-collapsed, length ≥ 5) appears as a substring of the collapsed input. The **longest** matching token wins; `OTHER` is never a keyword; tokens under 5 chars are ignored to avoid spurious hits.
- Enabled only on `excepts` (→ `carve_out`), `covers` (→ `covered_subject`), and `prohibits_damage` (→ `damage_type`) — exactly the lexically-anchored `_LIST_ENUM_DIMS` whose verbatim-phrase failure mode this is. **Scalar enum validators are untouched** (exact-only, no behavior change), which bounds the blast radius.
- Safe by construction: these three dims are lexically grounded (ADR-0028), so a spurious keyword hit whose cue is absent from the clause text is still downgraded to `AMBIGUOUS` by the grounding gate. The `≥ 5`-char + longest-match rules keep the enum values distinctive (indemnification, confidentiality, gross_negligence, …).

## Consequences

- **The condition is now detectable through typed matching.** Live-verified on the issue's reproduction: the Cap On Liability clause extracts `carve_out = indemnification` (EXTRACTED, grounded — "indemnification" is in the text), alongside its other correctly-extracted properties (cap_basis, cap_quantum, temporal_bound, …). "liability" / "total aggregate liability" correctly stay `OTHER` (no Subject token matches) — no false positive.
- **Symmetry restored:** the same value is now recoverable from a document as from a query, so retrieval no longer asks a question the corpus cannot answer. RuleWright's `xfail(strict=True)` on "uncapped indemnity" will XPASS.
- **Model choice unaffected:** granite produced the value once normalization was fixed, so no model swap (e.g. to qwen-3.8) was needed — the fix is deterministic normalization, not a prompt or model change.
- **Re-ingest to gain it on existing KGs:** the value is produced at ingest, so contracts already ingested need re-processing to acquire the `carve_out` edge (no store-side backfill can recover a value the ingest dropped).
- Only the three grounding-gated list dims opt in; extending the fallback to other list dims later is a one-flag change if a similar verbatim-phrase gap surfaces there.
