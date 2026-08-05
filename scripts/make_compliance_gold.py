"""RG-2 (compliance rung 2, roadmap C-7): the FIRST-PASS ad-claims GOLD set from public FTC decisions.

Each case: a subject-document (the advertiser's claim(s)) + a first-pass label (expected verdict + the 16 CFR 255
rule area + the FTC's finding + the source). RG-3 runs `compliance_check` over these and scores precision/recall.

HONESTY (read this):
- LLM-ASSISTED FIRST PASS, EXPERT-REVIEW PENDING. Labels are my best-judgment reading of public FTC press
  releases/case pages (cited per case); they are NOT an expert-graded legal benchmark. Final grading of
  "adequately substantiated / misleading" needs a legal SME. Treat the numbers as directional, not shippable.
- VIOLATION-SKEWED, by construction: the FTC acts on violations, so real public cases are mostly violations.
  The negative (compliant) class is thin -- one real no-action case + a few CONSTRUCTED-per-FTC-guidance
  examples (flagged `provenance="constructed"`) so precision (alert fatigue) is measurable at all.
- Subject texts are short, faithful paraphrases/quotes of the claims at issue (not the full ads).

  uv run --no-sync python -m scripts.make_compliance_gold
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path("data/compliance/gold_cases")

# provenance: "ftc_case" = a real public FTC action/closing letter (cited); "constructed" = authored per FTC
# compliance guidance to give the negative class (clearly not a real case). expected_verdict is the FIRST-PASS
# label. target_rule = the 16 CFR 255 area the engine should key on.
CASES: list[dict] = [
    # --- real FTC violations: substantiation / health / weight-loss ---
    {"id": "truheight_height", "provenance": "ftc_case", "case": "FTC v. TruHeight (2026)",
     "source": "https://www.ftc.gov/news-events/news/press-releases/2026/04/ftc-takes-action-against-truheight-deceptive-unsubstantiated-advertising-supposed-height-enhancing",
     "expected_verdict": "violation", "target_rule": "255.1 (health-claim substantiation)",
     "ftc_finding": "Unsubstantiated: the sole 'clinical evidence' was a single company-sponsored 32-subject study showing no meaningful difference between treatment and control.",
     "subject_text": "TruHeight is the only supplement clinically proven to help height growth in children and teens. Give your kids the clinically-backed edge to grow taller."},
    {"id": "amare_clinically_proven", "provenance": "ftc_case", "case": "FTC v. Amare Global (2026)",
     "source": "https://www.ftc.gov/news-events/news/press-releases/2026/06/ftc-files-contempt-motion-against-amare-global-three-individuals-over-unsubstantiated-health-claims",
     "expected_verdict": "violation", "target_rule": "255.1 (health-claim substantiation)",
     "ftc_finding": "No competent and reliable scientific evidence supports the 'science backed' / 'clinically proven' mental-wellness health claims.",
     "subject_text": "Amare's products are science backed and clinically proven to improve your mental wellness and gut-brain health."},
    {"id": "teami_detox", "provenance": "ftc_case", "case": "FTC v. Teami",
     "source": "https://www.ftc.gov/news-events/topics/truth-advertising/health-claims",
     "expected_verdict": "violation", "target_rule": "255.1 (weight-loss substantiation)",
     "ftc_finding": "No reliable scientific evidence that the 30 Day Detox Pack causes weight loss; FTC returned $930,000+ to consumers.",
     "subject_text": "The Teami 30 Day Detox Pack helps you lose weight, flush toxins, and boost your metabolism."},
    {"id": "roca_labs_weightloss", "provenance": "ftc_case", "case": "FTC v. Roca Labs",
     "source": "https://www.ftc.gov/weight-loss",
     "expected_verdict": "violation", "target_rule": "255.1 (weight-loss substantiation)",
     "ftc_finding": "Baseless weight-loss claims; court granted the FTC summary judgment.",
     "subject_text": "The Roca Labs formula makes you lose weight without surgery or dieting - guaranteed results."},
    {"id": "nextmed_weightloss", "provenance": "ftc_case", "case": "FTC v. NextMed (2025)",
     "source": "https://www.ftc.gov/news-events/news/press-releases/2025/12/ftc-approves-final-order-against-telehealth-provider-nextmed-over-charges-it-used-deceptive",
     "expected_verdict": "violation", "target_rule": "255.1 + 255.2 (unsubstantiated + fake testimonials)",
     "ftc_finding": "Unsubstantiated weight-loss claims plus fake testimonials and distortion of consumer reviews.",
     "subject_text": "Our GLP-1 program delivers dramatic weight loss - see the real results our patients rave about in their testimonials!"},
    # --- real FTC violations: endorsement / disclosure / reviews ---
    {"id": "aspartame_influencer_no_disclosure", "provenance": "ftc_case", "case": "FTC influencer warnings re aspartame/sugar (2023)",
     "source": "https://www.ftc.gov/news-events/news/press-releases/2023/11/ftc-warns-two-trade-associations-dozen-influencers-about-social-media-posts-promoting-consumption",
     "expected_verdict": "violation", "target_rule": "255.5 (undisclosed material connection)",
     "ftc_finding": "Influencers failed to adequately disclose that they were apparently hired to promote the safety/consumption of aspartame or sugar.",
     "subject_text": "As a registered dietitian, I enjoy aspartame-sweetened drinks every day - they're a safe, smart choice for a healthy lifestyle. So refreshing!"},
    {"id": "rytr_fake_reviews", "provenance": "ftc_case", "case": "FTC v. Rytr (2024)",
     "source": "https://www.ftc.gov/news-events/news/press-releases/2024/12/ftc-approves-final-order-against-rytr-seller-ai-testimonial-review-service-providing-subscribers",
     "expected_verdict": "violation", "target_rule": "255.2 (fake/deceptive consumer reviews)",
     "ftc_finding": "The AI service generated detailed reviews containing specific, material details bearing no relation to the user's actual experience - false/deceptive reviews.",
     "subject_text": "This product changed my life! After three weeks my chronic back pain vanished and I slept through the night - five stars, highly recommend!"},
    {"id": "truheight_incentivized_reviews", "provenance": "ftc_case", "case": "FTC v. TruHeight (2026) - reviews",
     "source": "https://www.ftc.gov/news-events/news/press-releases/2026/04/ftc-takes-action-against-truheight-deceptive-unsubstantiated-advertising-supposed-height-enhancing",
     "expected_verdict": "violation", "target_rule": "255.2 (fake/incentivized reviews)",
     "ftc_finding": "Amplified claims with fake and incentivized reviews presented as independent consumer experiences.",
     "subject_text": "See our thousands of verified 5-star reviews from real parents whose kids grew taller with TruHeight!"},
    # --- real NAD violations (advertiser's evidence found insufficient -> discontinue) ---
    {"id": "nad_pamprin_clinically_tested", "provenance": "nad_case", "case": "NAD - Pamprin Botanicals (2024)",
     "source": "https://bbbprograms.org/media/newsroom/decisions",
     "expected_verdict": "violation", "target_rule": "255.1 (substantiation - flawed study)",
     "ftc_finding": "NAD found the supporting study flawed (no control group, among other reasons) and recommended discontinuing the 'clinically tested'/'scientifically tested' claims.",
     "subject_text": "Pamprin Botanicals is clinically tested and scientifically tested to relieve your PMS symptoms."},
    {"id": "nad_willow_semaglutide", "provenance": "nad_case", "case": "NAD - Willow Health Services (2025)",
     "source": "https://bbbprograms.org/media/newsroom/decisions",
     "expected_verdict": "violation", "target_rule": "255.1 (health-claim substantiation)",
     "ftc_finding": "NAD determined the evidence was insufficient to support the health claims and recommended modifying/discontinuing them.",
     "subject_text": "Our compounded semaglutide tablets deliver dramatic, doctor-designed weight loss results."},
    {"id": "nad_vktry_superiority", "provenance": "nad_case", "case": "NAD - VKTRY Performance Insoles (2025), unsupported claims",
     "source": "https://bbbprograms.org/media/newsroom/decisions/vktry",
     "expected_verdict": "violation", "target_rule": "255.1 (superiority/performance substantiation)",
     "ftc_finding": "NAD recommended discontinuing the superiority/performance and instantaneous-results claims; the studies did not assess jump height or running speed.",
     "subject_text": "VKTRY insoles instantly make you jump higher and run faster than any other insole on the market."},
    # --- real NAD compliant: claims NAD determined were SUPPORTED (genuine substantiated claims) ---
    {"id": "nad_vktry_athletes_supported", "provenance": "nad_case", "case": "NAD - VKTRY (2025), supported claim",
     "source": "https://bbbprograms.org/media/newsroom/decisions/vktry",
     "expected_verdict": "compliant", "target_rule": "255.2 (testimonial/usage claim - supported)",
     "ftc_finding": "NAD determined the claim 'Worn by pro and college athletes' was SUPPORTED.",
     "subject_text": "VKTRY Performance Insoles are worn by pro and college athletes."},
    {"id": "nad_vktry_rnd_supported", "provenance": "nad_case", "case": "NAD - VKTRY (2025), supported claim",
     "source": "https://bbbprograms.org/media/newsroom/decisions/vktry",
     "expected_verdict": "compliant", "target_rule": "255.1 (objective claim - supported)",
     "ftc_finding": "NAD determined the claim 'Backed by 15 years of research & development' was SUPPORTED.",
     "subject_text": "VKTRY Performance Insoles are backed by 15 years of research and development."},
    {"id": "nad_vktry_apma_supported", "provenance": "nad_case", "case": "NAD - VKTRY (2025), supported claim",
     "source": "https://bbbprograms.org/media/newsroom/decisions/vktry",
     "expected_verdict": "compliant", "target_rule": "255.4 (organization acceptance - literal claim supported)",
     "ftc_finding": "NAD determined the literal claim 'Accepted by the American Podiatric Medical Association' was SUPPORTED (only the implied 'APMA recommends' was not conveyed).",
     "subject_text": "VKTRY Performance Insoles are accepted by the American Podiatric Medical Association."},
    # --- negative class: real FTC no-action closing letters (on-domain) ---
    {"id": "lifes_vigor_closed", "provenance": "ftc_case", "case": "FTC closing letter - Life's Vigor",
     "source": "https://www.ftc.gov/system/files/documents/closing_letters/lifes-vigor-et-al./010410lifevigorletter.pdf",
     "expected_verdict": "compliant", "target_rule": "255.1 (substantiation - no action)",
     "ftc_finding": "FTC staff closed the investigation into the herbal product's claims (no enforcement action). No-action != affirmative clearance; weak positive signal.",
     "subject_text": "Our chaparral herbal supplement is a traditional botanical that supports your overall wellness."},
    {"id": "advanced_bionutritionals_closed", "provenance": "ftc_case", "case": "FTC closing letter - Advanced Bionutritionals",
     "source": "https://www.ftc.gov/sites/default/files/documents/closing_letters/advanced-bionutritionals-et-al./050711advancedbio.pdf",
     "expected_verdict": "compliant", "target_rule": "255.1 (substantiation - no action)",
     "ftc_finding": "FTC staff CLOSED the investigation (no enforcement action) after reviewing the supplement's substantiation. (No-action != affirmative clearance; weak positive signal.)",
     "subject_text": "Our supplement supports healthy circulation. This statement has not been evaluated by the FDA; results may vary and it is not intended to diagnose, treat, cure, or prevent any disease."},
    # --- negative class: CONSTRUCTED per FTC compliance guidance (clearly not real cases) ---
    {"id": "constructed_disclosed_endorsement", "provenance": "constructed", "case": "constructed per FTC Endorsement Guides",
     "source": "https://www.ftc.gov/business-guidance/resources/disclosures-101-social-media-influencers",
     "expected_verdict": "compliant", "target_rule": "255.5 (material connection clearly disclosed)",
     "ftc_finding": "Constructed: a clearly-disclosed endorsement making only a subjective opinion with a results-vary qualifier - the compliant pattern in the FTC guidance.",
     "subject_text": "#ad - paid partnership with GlowCo. I genuinely like how this moisturizer makes my skin feel; this is my honest opinion. Individual results vary."},
    {"id": "constructed_substantiated_health", "provenance": "constructed", "case": "constructed per FTC Health Products Compliance Guidance",
     "source": "https://www.ftc.gov/business-guidance/resources/health-products-compliance-guidance",
     "expected_verdict": "compliant", "target_rule": "255.1 (competent and reliable scientific evidence cited)",
     "ftc_finding": "Constructed: an objective health claim backed by independent peer-reviewed RCT evidence with a qualifier - meets the substantiation standard.",
     "subject_text": "In two independent, placebo-controlled clinical trials published in peer-reviewed journals, participants taking JointEase reported reduced joint discomfort. Individual results may vary."},
    {"id": "constructed_no_objective_claim", "provenance": "constructed", "case": "constructed - no objective claim",
     "source": "https://www.ftc.gov/business-guidance/advertising-marketing/health-claims",
     "expected_verdict": "compliant", "target_rule": "255.1 (no objective/efficacy claim made)",
     "ftc_finding": "Constructed: pure subjective taste/experience puffery with no objective health or efficacy claim - nothing to substantiate.",
     "subject_text": "Our herbal tea has a smooth, calming flavor - a relaxing part of your evening routine."},
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for c in CASES:
        (OUT / f"{c['id']}.txt").write_text(c["subject_text"].strip() + "\n", encoding="utf-8")
    manifest = {
        "note": "FIRST-PASS ad-claims gold (roadmap C-7, RG-2 + NEG-GOLD). LLM-assisted from public FTC actions/"
                "closing letters + NAD press-release summaries (NAD 'supported' = compliant, 'discontinue' = "
                "violation); labels are my best-judgment first pass, EXPERT-REVIEW PENDING (not a shippable legal "
                "benchmark). NEG-GOLD grew the REAL negative class with NAD-supported claims + an FTC closing "
                "letter. provenance: ftc_case / nad_case (real, cited) vs constructed (authored for the negative "
                "class). Sources cited per case.",
        "standard": "FTC 16 CFR Part 255 (Endorsement Guides) + FTC substantiation doctrine",
        "counts": {"violation": sum(c["expected_verdict"] == "violation" for c in CASES),
                   "compliant": sum(c["expected_verdict"] == "compliant" for c in CASES),
                   "ftc_case": sum(c["provenance"] == "ftc_case" for c in CASES),
                   "nad_case": sum(c["provenance"] == "nad_case" for c in CASES),
                   "constructed": sum(c["provenance"] == "constructed" for c in CASES),
                   "real_compliant": sum(c["expected_verdict"] == "compliant" and c["provenance"] != "constructed"
                                         for c in CASES)},
        "cases": [{k: v for k, v in c.items() if k != "subject_text"} for c in CASES],
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"[gold] wrote {len(CASES)} cases -> {OUT}  ({manifest['counts']})", flush=True)


if __name__ == "__main__":
    main()
