# Engine issue 0037: narrow closed vocabularies drop verbatim values to OTHER, losing them

**Raised by:** RAG_Wright (engine, self-discovered during the issue-0036 Qwen A/B) · **Date:** 2026-09-12 · **Severity:** medium-high — silent recall loss on the multi-valued descriptive dimensions (carve-outs, covered subjects, damage types) that Leg-B / compliance read
**Affects:** `ontology/clause_template.py` (`_normalize_enum`, `Subject`/`ExceptionModel`/`DamageType` + the `covers`/`excepts`/`prohibits_damage` fields) · `spans/clause_kg_extractor.py` (`_LIST_ENUM_DIMS`, `_canonical_value`) · the ontology vocab (`contract_bridge.ttl` → `_generated_vocab.py`)
**Related:** issue 0033 (carve-out recall; added the keyword-substring fallback), ADR-0066 (ontology is the source of truth for closed vocabularies), ADR-0028 (lexical grounding gate)

---

## Symptom

During the issue-0036 clause A/B on Qwen, the logs were dominated by:

```
Unmapped enum value 'loss of profits' for DamageType; falling back to OTHER
Unmapped enum value 'the modification of the Software by a party other than Changepoint' for ExceptionModel; falling back to OTHER
Unmapped enum value 'Software' for Subject; falling back to OTHER
... (dozens per document)
```

The model extracted a **correct, verbatim** value from the clause; it just wasn't one of the handful of members in the closed enum, so `_normalize_enum` mapped it to `OTHER` — and `OTHER` is treated as "not asserted" (`_canonical_value` returns `None`), so the value is **discarded**. The clause records the *presence* of a carve-out/damage list but loses *what* it was.

## Root cause

Three multi-valued descriptive dimensions are modeled as **bare closed-enum lists with no free-text retention**:

| Clause field | dimension | closed vocab (size) | reality |
|---|---|---|---|
| `covers` | `covered_subject` | `Subject` (6) | open-ended — any subject of an obligation |
| `excepts` | `carve_out` | `ExceptionModel` (8) | open-ended — any carve-out subject |
| `prohibits_damage` | `damage_type` | `DamageType` (5) | closed backbone (indirect/consequential/incidental/punitive/special) **plus** a long tail of specific waivers ("loss of profits", "loss of use", "business interruption", "loss of data", "cost of cover", "lost revenue") |

The loss happens at the `Clause` validator (`_normalize_enum`), *before* `clause_to_record` builds assertions, so by the time the value would be stored it is already `OTHER`. The issue-0033 `keyword_fallback` only helps when the phrase *contains* a canonical token (e.g. "gross negligence" → `GROSS_NEGLIGENCE`); it cannot help a genuinely out-of-vocab value ("loss of profits" contains no `DamageType` token).

Contrast the fields that work: `cap_quantum`, `jurisdiction`, `commitment_quantum`, `ld_trigger` are **open string** dims (`_OPEN_STR_DIMS`) — they retain the verbatim phrase. `CapConstraint` pairs a closed enum (`cap_basis`) with an open slot (`cap_quantum`), which is the pattern that preserves both typed and long-tail information.

## Why it matters

These three dimensions are exactly the ones RuleWright's compliance / carve-out queries read (issue 0033 was about `carve_out` recall). A clause that waives "loss of profits and business interruption" currently records a `damage_type` list of `[OTHER, OTHER]` → both dropped → the waiver's *content* is invisible to any query. This is silent (no error, the clause exists) and model-independent (Qwen surfaced it because it extracts more verbatim detail, but granite loses the same tail).

`symbolic_validation` already leaves these three dims **unbounded** (not shape-checked) and they are lexically grounded (ADR-0028), so retaining the verbatim value is safe: a hallucinated value whose cue is absent from the text is still downgraded by the grounding gate.

## The fix — options in the companion note

The engine owns this. Fix options (retain verbatim on the open dims / enrich the ontology vocab for the closed backbone / hybrid) and the chosen approach are recorded in the resolving ADR.
