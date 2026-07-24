# ADR-0028: Deterministic property-grounding judge + Flash→Pro extraction cascade

Status: accepted (2026-07-24)
Relates to: ADR-0026 (property schema + AMBIGUOUS escape), ADR-0027 (provider routing), the model rule
(DeepSeek V4 Pro default; a benchmarked exception is allowed).

## Context

A 15-clause, 3-model bench (DeepSeek V4 Pro / Flash / Gemma 4) on hard, property-rich clauses found: Pro is
the most precise but throttled (~24s/call today) and even had a total-failure (0 props) case; Flash is ~5x
faster with the highest coverage but occasionally emits a confident hallucination (e.g. `carve_out=fraud` on
a clause with no "fraud"); Gemma is fast but dumps verbose non-vocab `AMBIGUOUS` values that pollute the
shared value nodes. For a SOFT-BOOST property layer, coverage matters more than precision, but confident
hallucinations add false matches.

Key observation: most of the property vocabulary maps to legal TERMS OF ART that must physically appear in the
clause. So whether an `EXTRACTED` value on such a dimension is grounded is checkable LOCALLY and
DETERMINISTICALLY -- no model, no network.

## Decision

**Build a deterministic grounding judge** (`spans/property_grounding.py`): a `dimension -> value -> surface
cues` map for the lexically-anchored dimensions (carve_out, covered_subject, damage_type, warranty_scope,
procedural, covered_parties, claim_scope:third_party); `is_grounded`, `ungrounded_assertions`,
`needs_escalation`, `reground`. Non-anchored (semantic/open) values are treated as grounded (the judge cannot
disprove them).

**Use it two ways (double duty):**
1. **Flash→Pro escalation cascade** for property extraction (T58b): run Flash for the bulk; re-extract only
   the clauses the judge flags (`needs_escalation`) with Pro. This concentrates the throttled Pro calls on the
   deterministically-suspect minority. Flash-as-bulk-extractor is the ADR-0026-style benchmarked exception to
   the model rule, gated by this verifier + Pro fallback.
2. **Permanent quality gate** on the final graph: `reground` downgrades any ungrounded `EXTRACTED` value to
   `AMBIGUOUS` (kept but down-weightable) regardless of which model produced it, so the value nodes stay clean.

## Consequences

- The throttle is largely dodged: bulk extraction is fast Flash, and Pro runs only on flagged clauses.
- **Asymmetric error cost:** in ESCALATION mode a false flag (a real value phrased with a synonym we did not
  list) costs one extra Pro call, never correctness. In GATE mode (`reground`) a false flag downgrades a real
  `EXTRACTED` value -> a correctness cost, so the cue set must stay reasonably complete; broad anchors
  (`applicable_law`/`violation_of_law` -> "law") are deliberately lenient to avoid over-flagging.
- **Limitation:** the judge only sees lexically-anchored dimensions. The SEMANTIC dimensions (mutuality,
  favorability, party_asymmetry, cap basis/quantum interpretation) carry no keyword and are NOT checked -- their
  errors pass through for both models (Pro also erred on mutuality in the bench, so escalation would not
  reliably fix them). Closing that gap would need a semantic check, out of scope here.

## References

- `spans/property_grounding.py`; tests `tests/spans/test_property_grounding.py`. The 3-model bench:
  `scripts/compare_extraction_models.py`. The double-duty quality-gate reminder is tracked as T61 in tasks.md
  and memory `property-grounding-judge`.
