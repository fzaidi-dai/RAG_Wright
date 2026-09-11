# RuleWright handoff: carve-out / damage / subject values are no longer dropped to OTHER

Date: 2026-09-12 · on `origin/main` (commit `b7198cf`) · engine issue 0037, ADR-0102 · **No API change. More values now populate the typed layer.**

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

## Note on model over-extraction

Qwen sometimes puts a damage term into `covers` too; those are now retained as AMBIGUOUS (before: silently dropped). They're flagged and excluded from the precision view. That's an extraction-quality matter (the model mis-categorizing), separate from this fix; flag it if it's noisy for you and we'll look at the prompt.

Reference: commit `b7198cf`, ADR-0102, engine issue `docs/engine-issues/0037-...`, `ontology/clause_template.py` (open fields), `spans/clause_kg_extractor.py::_open_list_value`, `ontology/contract_bridge.ttl` (skos:broader synonyms) → `_generated_vocab.VALUE_SYNONYMS`. Full suite: 1553 passed.
