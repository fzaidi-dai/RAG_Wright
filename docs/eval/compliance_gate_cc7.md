# CC-7 — Compliance rung-1 Track-1 eval gate

The eval gate for the ad-compliance engine (compliance module rung 1, roadmap §13.4 Track 1). Two parts: the
judgment node measured on a public proxy (ContractNLI), and the whole engine measured on the labeled ad
samples. Completes C-6 (which had measured only DeepSeek Flash) with the **product** model and a ceiling.

Model rule: the product judge is self-hosted **Granite-4.1-8b** (ADR-0039); DeepSeek is dev/eval reference.
Run via OpenRouter (A100 stopped) — model-determined numbers, substrate-invariant.

## Part A — judgment node on ContractNLI (balanced 150-pair slice, entailment/contradiction/neutral)

`entailment≈compliant`, `contradiction≈violation`, `neutral≈needs-review`. Full slice, no shortcut.

| Model | Accuracy | recall entail | recall **contradiction** (violation) | recall neutral | contradiction→entailment (the liability FN) |
|---|---|---|---|---|---|
| DeepSeek Flash (C-6 baseline) | 0.780 | 0.760 | 0.800 | 0.780 | 8/50 (16%) |
| **Granite-4.1-8b (PRODUCT)** | **0.773** | 0.780 | **0.800** | 0.740 | **3/50 (6%) — safest** |
| DeepSeek Pro (ceiling) | 0.793 | 0.760 | 0.840 | 0.780 | 5/50 (10%) |

Findings:
- The product judge (Granite) is **within 0.02 of the ceiling** and **ties Flash** — the open product model is
  judgment-class competitive.
- On the **safety-critical direction** (a violation read as supported = the liability false-negative), Granite is
  the **best of the three** (6% vs Flash 16% / Pro 10%). Escalation does not help here.
- No class collapse (every class recall ≥ 0.74).
- **The Granite→Pro escalation buys ~+0.02 accuracy and is WORSE on the liability direction** — so escalation is
  not the safety lever; the conservative default (uncertainty → needs_review) + per-finding human-gate are.

## Part B — the whole engine on the labeled ad samples (CC-0 manifest: 3 violation, 1 compliant)

`compliance_check` end-to-end (claim_extraction → applicability → Granite judge → report), ad-level signal =
"any VIOLATION finding". Capped to 3 §255.5 disclosure requirements (the CC-6 cross-product cap).

| Ad | Expected | Predicted | Verdict mix |
|---|---|---|---|
| influencer_skincare_no_disclosure | violation | **violation** | 3 violation / 6 needs_review |
| supplement_expert_endorsement | violation | **violation** | 4 violation / 5 needs_review |
| weightloss_testimonial | violation | **violation** | 5 violation / 4 needs_review |
| compliant_disclosed_ad | compliant | **compliant** | 5 compliant / 4 needs_review (0 violation) |

**Ad-level: 4/4 correct.** The compliant ad produces zero violations (the ad-level disclosure aggregation, CC-6);
the violators are all flagged.

## The GATE bar (set here)

Ship the judgment node / engine when, on the Track-1 proxy + labeled smoke:
1. Judgment-node verdict **accuracy ≥ 0.75** on the balanced slice. — Granite 0.773 ✓
2. **Violation-class recall ≥ 0.80.** — Granite 0.800 ✓
3. **Violation-read-as-supported ≤ 10%** (the liability direction). — Granite 6% ✓
4. **No class collapse** (each class recall ≥ 0.70). — ✓
5. **Engine ad-level:** flags every known-violator ad, passes every known-compliant ad. — 4/4 ✓
6. **Conservative default** (uncertainty → needs_review) + **per-finding human-gate** — design guarantees (CC-4).

**Decision:** ship **Granite** as the product judge default (at ceiling, safest on the liability direction).
**Do NOT enable Granite→Pro escalation by default** (buys +0.02, worse on safety) — the conservative-default +
human-gate is the safety mechanism. Revisit escalation only if rung-2 gold shows a real gap.

## Rung-1 exit + what's next

Rung 1 (the ad-compliance engine) is **built, registered** (requirement_extraction, claim_extraction,
compliance_judgment, compliance_ingestion, compliance_check + the Requirement/Claim/Verdict contracts +
compliance_bridge.ttl) and **clears the Track-1 gate**. Deferred / next:
- **Main engineering item:** CC-6 **semantic narrowing** (Leg-B top-k most-relevant requirement per claim +
  requirement dedup) to cut the redundant-rule `needs_review` noise and the broad cross-product. See
  [[ontology-lever-vs-extraction-lever]].
- **Rung 2 (roadmap C-7):** real NAD/FTC ad-claims gold — measure precision/recall on domain data; the honest
  domain investment. The Track-1 proxy + 4-ad smoke are a de-risk, not the domain gold.
- Extraction-quality tuning: 255.0 definitions over-generation (prompt/corpus lever); empty applicability scope.
