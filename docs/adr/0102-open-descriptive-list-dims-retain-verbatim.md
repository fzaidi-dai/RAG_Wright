# ADR-0102: open descriptive list-dims retain verbatim; damage synonyms via the ontology

**Status:** accepted · **Date:** 2026-09-12 · **Resolves:** engine issue 0037 · **Related:** ADR-0066 (ontology is the source of truth), ADR-0033 (carve-out recall / keyword fallback), ADR-0028 (lexical grounding gate)

## Context

Three multi-valued descriptive dimensions — `covered_subject` (`covers`), `carve_out` (`excepts`), `damage_type` (`prohibits_damage`) — were modeled as bare closed-enum lists (`List[Subject/ExceptionModel/DamageType]`). A real extracted value outside the narrow vocab ("loss of profits", "the modification of the Software", "the Software") collapsed to `OTHER` at the `Clause` validator, and `OTHER` is dropped (`_canonical_value` → None). The verbatim value — the actual carve-out / damage / subject — was **silently discarded**. On the issue-0036 Qwen A/B this was pervasive (dozens of drops per document), and it hit exactly the fields RuleWright's compliance / carve-out queries read. These dims are open in reality (unbounded subjects / carve-outs; a closed damage *backbone* plus a long tail of specific waivers).

The user chose the **hybrid** fix: typed where the ontology knows the value, verbatim where it doesn't.

## Decision

1. **Capture verbatim (open the three fields).** `covers` / `excepts` / `prohibits_damage` are now `List[str]` on the `Clause` template — the model's verbatim phrases, cleaned (`_clean_open_list`: strip, drop empties / leaked prose > 120 chars or containing a tag). This is a documented DEVIATION of the extraction template from the closed dimension (like `document_reference`, ADR-0037): the *dimension* stays closed in the ontology (canonical backbone preserved in `VOCAB`/`CLOSED_VOCAB`), the *template field* captures open.

2. **Canonicalize at the KG boundary** (`clause_kg_extractor._open_list_value`), not the validator: exact/keyword vocab match → the canonical value (EXTRACTED); else an ontology `skos:broader` synonym → the canonical value (EXTRACTED); else the **verbatim phrase is kept** as an **AMBIGUOUS** assertion — the `PropertyAssertion` contract's sanctioned "other escape" for genuinely-novel values (kept + flagged, dropped only by the high-precision view). Nothing is discarded.

3. **Damage synonyms live in the ontology** (ADR-0066). `contract_bridge.ttl` gains `skos:broader` triples for the common consequential-damages tail (loss_of_profits, loss_of_use, business_interruption, loss_of_data, cost_of_cover, lost_revenue, lost_savings → `consequential`, with `skos:altLabel` surface variants). The loader parses them into `value_synonyms` and the generator emits `VALUE_SYNONYMS` into `_generated_vocab.py`; the boundary reads that map. Adding a synonym is a ttl edit + regenerate, never a code literal.

## Consequences

- **Recall: nothing is lost.** Live-verified on a damage-waiver clause: `indirect/special/incidental/consequential` typed EXTRACTED; the five specific waivers all mapped to `consequential` via the synonyms (EXTRACTED); carve-outs keyword-mapped to `indemnification`/`confidentiality`. Every value that previously vanished is now retained and largely typed.
- **Confidence semantics.** A retained out-of-vocab value is AMBIGUOUS (the contract's rule; kept in the full view, excluded from the grounded/citation precision view). Canonical and synonym-mapped values are EXTRACTED. A verbatim value is grounded (read from text), so the ADR-0028 gate keeps it; a spurious one whose cue is absent is still downgraded.
- **Introspection gained a `list_str` field kind**; the ttl template-capture for the three fields is `list_str` (no `enumClass`), kept in sync by `test_template_captured_in_ttl`.
- **Value-node dedup** (store, by (dimension, value)) collapses the several tail terms that map to the same canonical `consequential` into one edge.
- Model over-extraction into `covers` (e.g. damage terms) is now retained as AMBIGUOUS rather than silently dropped — slightly noisier in the full view, filtered by the precision view; a separate extraction-quality matter.

Full suite: 1553 passed, 44 skipped.

## Correction: `covered_subject` is CLOSED, not open (issue 0040)

This ADR made all three of `covered_subject` / `carve_out` / `damage_type` open (verbatim-retaining). That was an over-generalization. `Subject` — the `covered_subject` vocabulary — is a narrow **conduct** set (`fraud`, `gross_negligence`, `ip_infringement`, `trademark`, `copyright`, `violation_of_law`): it answers "what conduct survives the cap," meaningful for an indemnity/liability provision and meaningless for a forecasting/delivery/notices one. But the thematic group carrying `covers` runs on **every** provision (0038 extracts whole provisions; ADR-0101 removed the aspect gate), so the model answers "what does this provision cover?" with the contract's defined terms and parties (`API`, `HOVIONE`, `Agreement`). With verbatim retention those became **served AMBIGUOUS noise** — a clean post-0039 ingest measured `covered_subject` at **354 AMBIGUOUS : 1 EXTRACTED** (262 distinct values, on 67/68 clauses), unshippable. `carve_out` (12:186) and `damage_type` (4:8) stay fine — their tails are genuinely open; adding `skos:broader` synonyms can't help `covered_subject` because `API`/`HOVIONE` are not under-specified subjects, they are answers to a different question.

Fix (issue 0040): `covered_subject` is reverted to a **closed** dimension. `covers` is `List[Subject]` again with the closed `_normalize_enum` validator (keyword fallback keeps a quoted conduct phrase), and it moves back from `_OPEN_LIST_DIMS` to `_LIST_ENUM_DIMS` in `clause_kg_extractor` — an out-of-vocab value maps to `OTHER` and is dropped (not retained). This also self-scopes the dimension: a provision whose content isn't conduct produces no `covered_subject`, without an applicability gate or a function dependency. Restoring `List[Subject]` additionally puts the closed vocab back in the tag-parse prompt as a hint, nudging the model toward the conduct register. `carve_out`/`damage_type` are unchanged (0037 stands where it works). Live spot-check: `covered_subject` is now empty on forecasting/announcement provisions (the noise is gone); conduct values on an indemnity clause still land as EXTRACTED. Full suite: 1562 passed.
