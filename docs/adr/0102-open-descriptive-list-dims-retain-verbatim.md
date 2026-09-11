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
