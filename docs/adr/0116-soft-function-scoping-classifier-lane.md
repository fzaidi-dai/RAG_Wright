# ADR-0116: Soft function-scoping for the classifier lane (the "classifiers can't abstain" fix)

## Status
Accepted (2026-09-30). Refines ADR-0115 (classifier-only Step-3a): the classifier lane is no longer purely
function-INDEPENDENT — it is soft-SCOPED by the clause function. Builds on the function soft-tag (ADR-0047/0082,
`function-classification-not-load-bearing`).

## Context
ADR-0115 made Step-3a classifier-first and function-independent: every covered dim's classifier runs on every
span. The CLS-D live run exposed a precision failure this creates. **A trained classifier cannot say "not
present"** — it always emits its top-k, unlike the LLM tag-parse it replaced (which omitted properties the span
did not state). The ADR-0028 lexical grounding gate prunes spurious values only for **lexically-anchored** dims;
the **subjective/relational** dims (mutuality, favorability, party_asymmetry, nonsolicit_target, ip_ownership,
law_multiplicity, mfn_scope, …) have no cue to reground, so confident-but-wrong tags survive. On one cap-on-
liability clause the record held **38 assertions across all 21 dims**, ~29 of them spurious (nonsolicit=employees,
ip_ownership=retained, …). This is a KG-precision problem, not a wiring bug; and reverting to the LLM was ruled
out (ADR-0115 is not reversible for these dims).

We considered three fixes: (A) soft function-scoping, (B) an abstain / NONE-class retraining, (C) a confidence
threshold. (C) fails because the spurious predictions are high-confidence (e.g. 0.998). (B) is the more complete
ML fix but needs new per-dim negative data. (A) is immediate and reuses existing infrastructure.

## Decision
**Soft function-scoping.** Before running the classifier lane, scope it to `scoped_dims(functions)` — the UNION of
`dimensions_for(f)` over the clause's **top-3 function soft-tags** (from the segment-stage SetFit function
classifier's ranked scores). A dim's classifier runs only if it is applicable to at least one of those functions.

- It is **soft**: unioning over the top-3 (not gating on top-1) tolerates the ~0.5 top-1 / ~0.59 top-3 function
  accuracy — the right function is usually in the top-3 even when top-1 is wrong. The function remains a soft
  tag, never a hard gate (consistent with ADR-0047/0082).
- A **genuinely untagged** provision (no real function in its top-k) scopes to `None` → every dim runs (the
  function-independent fallback), accepting over-emission only on that minority.
- The residual LLM lane (the 7 numeric dims) is unaffected — it always runs its one call.

## Consequences
- The cap-clause record dropped from **38 → 9 assertions, all relevant**; a real-NDA end-to-end run held ~4.8
  assertions/provision (vs ~38 unscoped). Precision restored without a revert.
- The classifier lane is now function-AWARE-but-soft, a deliberate, principled walk-back from ADR-0115's
  "function-independent" framing — scoping, not gating.
- `scoped_dims` / `FUNCTION_DIMENSIONS` are code for now (candidate for ontology migration, ADR-0066), like
  `CLAUSE_GROUPS`.
- **Complementary future direction (B), planned for CLS-F:** add a NONE/negative class so each classifier can
  ABSTAIN intrinsically. We pilot it on the 8 corpus-starved dims we are sourcing data for anyway (they need a
  train set regardless); soft-scoping stays intact meanwhile. If abstain-via-negatives proves out on the 8,
  consider retraining the current 21 with negatives too — which could then let us relax scoping.
