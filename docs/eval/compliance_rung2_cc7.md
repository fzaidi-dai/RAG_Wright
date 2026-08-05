# Rung 2 (roadmap C-7) — first-pass ad-claims gold + precision/recall

The domain eval for the compliance engine: measure it on REAL ad-compliance decisions, reporting precision
(alert fatigue) and recall (missed violations) separately (roadmap §13.4 Track 2). First increment: acquire
public FTC cases (RG-1), build a first-pass gold (RG-2), score `compliance_check` (RG-3/RG-4).

Artifacts: `scripts/make_compliance_gold.py` (the gold builder), `scripts/eval_compliance_gold.py` (the scorer).
Data stays gitignored; the builder is the source of truth.

## The gold (RG-1 + RG-2)

12 cases from public FTC decisions: **8 violation / 4 compliant; 9 real FTC cases + 3 constructed**.
Violations span the rule areas: substantiation/health (TruHeight, Amare, Teami, Roca, NextMed), undisclosed
material connection (aspartame influencers, §255.5), fake/incentivized reviews (Rytr, TruHeight, §255.2). The
negative class is one real FTC no-action closing letter + three constructed-per-FTC-guidance compliant examples.

**Honesty:** LLM-assisted first pass, EXPERT-REVIEW PENDING — best-judgment labels from public FTC press releases
(cited per case), not an expert-graded legal benchmark. Violation-skewed by construction (the FTC acts on
violations); the compliant class is thin + partly constructed. Numbers are DIRECTIONAL.

## Scores (RG-4) — violation class

| Subset | n | Recall (missed violations) | Precision (alert fatigue) | Acc |
|---|---|---|---|---|
| **Real FTC cases** | 9 | **1.00** (8/8, 0 missed) | **0.89** (1 FP) | 0.89 |
| Constructed compliant | 3 | — | 0.00 (2 FP) | 0.33 |
| **All** | 12 | **1.00** | 0.73 | 0.75 |

Full KG (155 rules), CC-8 narrowing on (k=5), Granite judge via the seam, LLM-CALL-TIMEOUT so a stalled call
can't hang the eval (it didn't).

## Findings

- **Recall = 1.00 on real FTC cases — the safety-critical metric is perfect.** Every one of the 8 real
  violations was flagged; zero missed. The engine is recall-strong (a missed violation = liability).
- **Precision is the weakness (alert fatigue) — the substantiation over-flag.** 3 false positives, all from the
  judge over-reading subjective / puffery / properly-disclosed claims as unsubstantiated violations:
  - `constructed_no_objective_claim` ("herbal tea has a calming flavor… relaxing part of your evening") → 12
    violation findings (puffery misread as a health claim);
  - `constructed_disclosed_endorsement` (a properly `#ad`-disclosed opinion) → 6 violations;
  - `advanced_bionutritionals_closed` (the weak real no-action case) → 4 violations.
  The conservative human-gate catches all of these (nothing false ships), but it is noise.

## Caveats + next

- **Precision is measured on a tiny negative class (4 compliant, 3 constructed) -> directional/noisy, not a real
  precision number.** A trustworthy precision figure needs a larger REAL compliant/negative set (NAD
  "substantiated" decisions, subscription-gated). Recall is on 8 real violations and is solid.
- **Tuning target (the clear next rung-2 step):** make the judge distinguish OBJECTIVE claims (need
  substantiation) from SUBJECTIVE opinion / puffery / properly-disclosed claims (don't). Levers: a judge-prompt
  refinement, and/or a claim-type gate (only run the substantiation check on objective claim types), and/or
  escalation on borderline. Measure the lift against a larger negative gold.
- Expert grading of the gold remains a human (legal-SME) step before any of this is a shippable benchmark.
