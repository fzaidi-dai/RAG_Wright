---
name: extraction_semantic_judge
description: >
  The clause-property faithfulness method: given ONE extracted property (a dimension = a value) and the clause
  text it was extracted from, decide whether a careful reading of THIS clause genuinely SUPPORTS that property.
  For the closed SEMANTIC dimensions (mutuality, favorability, party_asymmetry, cap_basis, the consent regimes)
  whose value is a reading with no surface token, this is what the lexical and symbolic gates cannot reach.
  Layer 3 of the neuro-symbolic extraction-fidelity cascade (ADR-0040). The applying capability owns the
  deterministic guarantees (which dimensions are semantic, the AMBIGUOUS downgrade, concurrency) -- this skill
  teaches only the reading.
---

# Extraction semantic judge: does this clause support this property?

This skill teaches a **method**, not a behavior. It extends the grounding-judge idea (ADR-0028) from
"is X supported by cue Y in the text?" to the harder **semantic** case: "does a faithful reading of THIS clause
support the property (dimension = value) that was extracted from it?" A capability applies it with its own
contract (`ClausePropertyRecord`) and its own guarantees; those guarantees are the **applying capability's**
job, not the method's (see "What this skill does NOT own").

## What you are judging

You are given one extracted **property** as `dimension = value` (with a short plain-language meaning of what
that property claims about the clause) and the **clause text**. These are the closed SEMANTIC dimensions:
`mutuality` (obligation runs both ways vs one), `favorability` (which side a term favors), `party_asymmetry`,
`cap_basis` (fixed_fee vs multiple_of_fees), the governing-law multiplicity, IP ownership, the non-solicit
target, the renewal mechanism, the change-of-control / assignment consent regimes, the MFN scope, the
termination right. Their value is a **reading** of the clause, not a token you can grep for -- which is exactly
why a model is spent here and nowhere else in the cascade.

## The one hard rule: strictness

Decide **supported=true** only if the clause **genuinely** supports the property. Decide **supported=false**
if the clause does not support it or contradicts it. Be strict:

- **mere plausibility is not support** -- that a mutual reading is *possible* is not enough; the clause must
  actually bear it;
- **absence of support in this clause means supported=false** -- if the text is silent on what the property
  asserts, it is not supported. Do not import world knowledge or the "usual" drafting; judge only THIS clause.

A `false` verdict means the reading is unsupported by the text; the applying capability will downgrade that
assertion to AMBIGUOUS (kept but flagged), never delete it. Give a one-sentence reason.

## What this skill does NOT own (the applying capability's job)

- **which dimensions are semantic** -- the set of dimensions this judge runs on (closed vocabulary, no lexical
  cue) is selected deterministically by the capability, not decided here;
- the **AMBIGUOUS downgrade** and the "leave untouched on a judge error / no ruling" conservative rule --
  applied by the capability, not by this method;
- the **per-assertion dispatch and concurrency** -- the capability runs this reading over each surviving
  (non-AMBIGUOUS) semantic assertion; the method judges exactly one at a time.
