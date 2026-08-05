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

## RG-5 — judge precision tuning (the substantiation over-flag fix)

Tuned the `compliance_judgment` prompt (all runs = granite-4.1-8b via the seam) to attack the precision gap.
Iterated eval-driven, twice:

| Judge prompt | Real-FTC recall | Real-FTC precision | Overall acc (12) | Constructed compliant |
|---|---|---|---|---|
| baseline | 1.00 | 0.89 | 0.75 | 0/3 (2 FP) |
| v1: exempt subjective/puffery/disclosed/substantiated | **0.88** ✗ | 0.88 | 0.83 | 3/3 ✓ |
| **v2 (adopted)** | **1.00** ✓ | **0.89** | **0.92** | **3/3** ✓ |

- **v1 over-corrected**: it fixed the synthetic puffery/disclosed false positives but LOST recall on a real
  violation -- Amare's *"science backed / clinically proven"* flipped to compliant, because the judge read the
  proof-language as if substantiation were provided. For a recall-critical product that is a bad trade.
- **v2 (adopted)** adds the distinction that fixes it: subjective opinion / puffery / a properly-disclosed
  endorsement is NOT a violation, BUT proof-adjectives ('clinically proven', 'science backed') WITHOUT a cited
  study ARE the unsubstantiated claim -> violation. Result: **recall 1.00 preserved, all 3 constructed
  false-positives fixed, overall accuracy 0.75 -> 0.92**, with a single remaining FP that is a weak gold label
  (the no-action closing letter, not an affirmative clearance).

These are general principles, not overfit to specific cases -- but the gold is still small (12, partly
constructed), so a robust precision number needs a larger real negative set. Recall (8 real violations, 0
missed) is the trustworthy signal.

## NEG-GOLD — grow the REAL negative class (19 cases) and the ceiling it revealed

Grew the gold to 19 (11 violation / 8 compliant), and crucially the REAL negative class 1 -> 5 by adding NAD
press-release decisions: NAD-**supported** claims = compliant (VKTRY "worn by pro/college athletes", "backed by
15 yrs R&D", "accepted by APMA"), NAD-**discontinue** = violation (Pamprin "clinically tested" [flawed study],
Willow semaglutide, VKTRY superiority), plus a second FTC closing letter (Life's Vigor).

Scores (tuned v2 judge, granite-4.1-8b):

| Subset | n | Recall | Precision | Acc |
|---|---|---|---|---|
| ALL | 19 | **1.00** (11/11, 0 missed) | 0.73 | 0.79 |
| constructed (substantiation visible in text) | 3 | — | — | **1.00** |

**Recall stays 1.00** -- every one of the 11 real violations flagged. **Precision fell to 0.73** on the bigger
negative class, and the 4 false positives are a REAL, non-tunable finding:
- 2 are NAD-**supported** OBJECTIVE claims ("worn by pro athletes", "accepted by APMA"). The engine judges the
  claim vs the rule FROM THE AD TEXT ALONE, but whether an objective claim is actually SUBSTANTIATED depends on
  EXTERNAL evidence the advertiser holds (the studies NAD reviewed). A supported objective claim looks identical
  to an unsubstantiated one in the ad text, so it is flagged -- arguably CORRECT assistive behavior (flag the
  objective claim for a human to verify the substantiation file; all are human-gated), but a FP against a
  "supported" label. The 3 CONSTRUCTED negatives (substantiation/disclosure visible in the text) all pass,
  confirming the judge is right when it can see the evidence.
- 2 are FTC no-action CLOSING LETTERS -- weak "compliant" labels (closed != affirmatively cleared).

So **0.73 is the honest precision ceiling on real compliant claims, limited by the external-substantiation
problem -- not a prompt-tuning gap.** The lever is architectural (give the judge access to the advertiser's
substantiation evidence) or procedural (an objective claim with no in-text substantiation -> needs_review +
human verifies), not more prompt-tuning on this gold. Recall (11 real violations, 0 missed) remains the
trustworthy, shippable-direction signal.

## EXTRACT-TUNE — drop the definitions section (KG hygiene, confirms it wasn't the FP driver)

Diagnostic on the 155-rule KG: §255.0 "Purpose and definitions" over-generated 61 "requirements"; 32 of them
carried a granite-extracted `endorsement` scope, so they DID leak into the applicable pool for endorsement
claims (a real leak surface + ~40% KG bloat) -- though narrowing's top-k already filtered them out of judging
(they rank low in cosine to real claims). Fix: `RegulationAdapter(skip_definitions=True)` skips any section whose
heading contains "definition" (universally non-operative; the robust general version is a deontic-cue/SHACL
validity gate). Re-ingested `ragwright_compliance`: **155 -> 96 requirements** (0 dead-lettered).

Re-eval on the 96-rule KG (19-case gold, tuned judge): **recall 1.00 HELD (11/11, 0 missed)**; precision 0.69
(vs 0.73 on the 155 KG -- flat within judge run-to-run variance + the shifted narrowing pool). So the
definitions-skip is a **KG-hygiene / robustness / cost win** (leaner KG, no leak surface, fewer candidate rules
per query) that **confirmed the definitions were NOT driving the false positives** -- the precision ceiling is
the external-substantiation problem above, not the definitions. Part (b), empty applicability scope (now 31/96),
is already handled at query time by the section->claim_type map (CC-8) -- no change needed.

## RG-6 — the procedural fix: objective claim + no in-text evidence -> needs_review

The NEG-GOLD ceiling (you cannot verify an objective claim's substantiation from ad text alone) is addressed
PROCEDURALLY, not by pretending to know: (1) judge prompt reserves VIOLATION for what is clearly wrong in the
text (OVERCLAIMING proof -- 'clinically proven'/'guaranteed' -- without a cited study; a MISSING required
disclosure; a fake review) and sends an unverifiable objective claim to NEEDS_REVIEW (escalate -- a human checks
the substantiation file), never clearing it; (2) an ad-level rollup `ComplianceReport.verdict`: VIOLATION only
when violation findings are a real signal (>= 2, so one spurious finding among many rules does not hard-flag),
else NEEDS_REVIEW if anything fired, else COMPLIANT.

3-way scores across the iterations (19-case gold, 96-rule KG, granite judge). CLEARED = a real violation the
engine silently passed = the true miss; hard-FP = a compliant case hard-flagged VIOLATION.

| Judge / rollup | clearance-safety (never clear a violation) | hard-violation precision | hard-FP rate |
|---|---|---|---|
| v2 (2-way) | 1.00 | 0.73 | 0.63 |
| procedural (3-way, any-violation) | 1.00 | 0.79 | 0.38 |
| **+ >=2 threshold (adopted)** | **1.00** | **0.92** | **0.12** |

- **Clearance safety 1.00** -- 0 of 11 real violations cleared; each fires >= 2 violation findings, so the
  threshold keeps recall.
- **Hard false positives 1/8** -- the NAD-**supported** objective claims now ESCALATE to needs_review (the honest
  "can't verify from text -> human checks the evidence"), not hard-flag. The lone residual hard-FP is the weak
  `lifes_vigor` closing-letter label.
- **Tradeoff:** 6 compliant cases escalate to needs_review (more human-review load) -- correct for an assistive,
  human-gated tool. The threshold lives on the product (`ComplianceReport.verdict`), so any consumer gets the
  principled ad-level verdict, not the twitchy raw counts.

This is the honest end-state on this gold: recall/clearance-safety complete, hard-precision 0.92, the residual
limits are the external-substantiation reality and the small/weak gold labels -- addressed next by a larger
expert-graded negative set, not more tuning.
