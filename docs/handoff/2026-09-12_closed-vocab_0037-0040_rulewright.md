# RuleWright handoff: closed-vocab retention — carve-out/damage kept verbatim (0037), covered_subject closed again (0040)

Date: 2026-09-12 · on `origin/main` (commit `1617867`) · engine issues 0037 + 0040, ADR-0102 · **No API change.**

Covers **0037** (carve-out/damage/subject values no longer dropped to OTHER) and its **0040** correction (`covered_subject` is a closed conduct vocab, reverted to dropping out-of-vocab). Read the 0040 section as the current state for `covered_subject`.

---

## What was wrong

`covered_subject` (`covers`), `carve_out` (`excepts`), and `damage_type` (`prohibits_damage`) were bare closed-enum lists. When the model extracted a real value outside the small vocab — "loss of profits", "the modification of the Software", any carve-out subject not in the 8-member enum — it collapsed to `OTHER` and was **discarded**. The clause recorded *that* it had a carve-out/damage list but not *what*. This hit exactly the fields your carve-out / compliance queries read, and it was pervasive (we found it in the issue-0036 Qwen A/B).

## What changed (hybrid: typed where known, verbatim where not)

These three dims now **retain the value**:
1. **Canonical match** (exact or keyword) → the canonical vocab value, **EXTRACTED**. Unchanged for you.
2. **Known synonym** (damage tail) → its canonical value, **EXTRACTED**. New: `loss of profits`, `loss of use`, `business interruption`, `loss of data`, `cost of cover`, `lost revenue`, `lost savings` now map to `consequential` (ontology `skos:broader`).
3. **Genuinely novel value** → kept **verbatim** as an **AMBIGUOUS** assertion (the contract's "other escape") instead of being dropped.

Live-verified on a damage-waiver clause: `IN NO EVENT ... INDIRECT, SPECIAL, INCIDENTAL OR CONSEQUENTIAL DAMAGES, INCLUDING LOSS OF PROFITS, LOSS OF USE, BUSINESS INTERRUPTION, LOSS OF DATA, OR COST OF COVER, except ... indemnification ... confidentiality` →
- `damage_type`: indirect, special, incidental, consequential (canonical) + all five specific waivers mapped to **consequential** — nine values, previously **zero** (all OTHER-dropped).
- `carve_out`: indemnification, confidentiality.

## What this means for you

- **More `contract_terms` / carve-out results, and they carry real content.** Nothing you queried before regresses; you gain the previously-invisible tail.
- **Confidence to expect:** canonical + synonym-mapped values are `EXTRACTED`; a novel verbatim value is `AMBIGUOUS`. If you use the high-precision (grounded-only) view, AMBIGUOUS values are excluded there — they are present in the full `contract_clause_index` view. If your carve-out surfacing wants the novel tail, read the full view (or treat AMBIGUOUS as "kept, lower-confidence"), not grounded-only.
- **Duplicates collapse:** several tail terms mapping to `consequential` become one value node (store dedups by (dimension, value)).

## Extending the damage synonyms

The synonym map is ontology-driven (ADR-0066): it lives as `skos:broader` triples in `contract_bridge.ttl`, generated into `VALUE_SYNONYMS`. If you see a recurring damage/subject/carve-out surface term you want normalized onto a canonical value (rather than kept verbatim), tell us the term + its canonical target and we add one ttl line + regenerate — no code change.

## Correction: `covered_subject` is CLOSED, not open (issue 0040)

You were right that `covered_subject` at **354 AMBIGUOUS : 1 EXTRACTED** is unshippable, and right about why: I over-generalized in 0037 by making all three dims open. `Subject` is a narrow **conduct** vocabulary (`fraud`, `gross_negligence`, `ip_infringement`, `trademark`, `copyright`, `violation_of_law`) — it answers "what conduct survives the cap," which is meaningless for a forecasting/delivery/notices provision. Since `covers` is asked of every provision (0038 extracts whole provisions; the aspect gate is gone), the model answered with the contract's defined terms and parties (`API`, `HOVIONE`, `Agreement`), and 0037 then *served* that as AMBIGUOUS noise. 0037 revealed it; it didn't create it.

Fixed: **`covered_subject` is now closed again.** An out-of-vocab value maps to `OTHER` and is **dropped** (not retained) — so the 354 noise values vanish and only real conduct matches survive as EXTRACTED. This also self-scopes the dimension (a non-conduct provision yields no `covered_subject`) without an applicability gate or function dependency, and restoring the closed vocab to the prompt nudges the model toward the conduct register. Live spot-check on the INTERSECT contract: `covered_subject` is now **empty** on forecasting/announcement provisions (was `API`/`HOVIONE`/…); a real indemnity clause still yields `fraud`/`gross_negligence` as EXTRACTED.

**`carve_out` (12:186) and `damage_type` (4:8) are unchanged** — those are genuinely open, their tails are real carve-out/damage language, and 0037 stands where it's working. If you want `covered_subject` to capture conduct phrased outside the 6-member vocab, that's a vocabulary-enrichment conversation (ttl `skos` additions), not verbatim retention — tell us the recurring conduct terms and we add them.

Reference: 0037 (ADR-0102) + 0040 (ADR-0102 correction), `ontology/clause_template.py::Clause.covers` (now `List[Subject]`, closed), `spans/clause_kg_extractor.py` (`covers` back in `_LIST_ENUM_DIMS`), `ontology/contract_bridge.ttl` (`covers` fieldKind `list_enum`). Full suite: 1562 passed.

## Note on model over-extraction

Qwen sometimes puts a damage term into `covers`; with `covered_subject` now closed those non-conduct values simply drop (they don't reach the typed layer). That's the intended behavior — a mis-categorized value on a closed dimension is noise, not a novel value.
