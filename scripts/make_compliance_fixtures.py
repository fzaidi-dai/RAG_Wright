"""CC-0 (compliance rung 1, roadmap §13): author the SYNTHETIC ad subject-doc smoke fixtures.

The regulatory side (FTC 16 CFR 255) is acquired by `acquire_ftc_255.py`; this writes the SUBJECT-document
side -- small synthetic advertisements that deliberately exercise specific 255 rules, so CC-3 (claim
extraction) and CC-6 (compliance_check) have testable inputs with a known expected signal. They are committed
as CODE (not data -- `/data/` is gitignored per convention) so a fresh clone regenerates the exact fixtures.
Real graded gold comes from NAD/FTC decisions in rung 2 (C-7); these are fictional (brands/people, *.example).

  uv run --no-sync python -m scripts.make_compliance_fixtures
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path("data/compliance/subject_samples")

SAMPLES: dict[str, str] = {
    "influencer_skincare_no_disclosure.txt": (
        "Obsessed with my glow lately! ✨ I've been using LumaGlow Retinol Serum every night for the\n"
        "past two weeks and the results are UNREAL. This stuff is clinically proven to erase deep\n"
        "wrinkles in just 7 days and completely reverse sun damage. My dermatologist could not believe\n"
        "it was over the counter. If you struggle with aging skin, you NEED this in your routine.\n\n"
        "Grab yours at lumaglow.example with code GLOW20 for 20% off. Trust me, your skin will thank you!\n"
    ),
    "supplement_expert_endorsement.txt": (
        "CardioPure Omega-3: The Heart Support Doctors Recommend\n\n"
        "\"As a cardiologist with over 20 years of experience, I can say CardioPure is the single most\n"
        "effective supplement for lowering cholesterol and preventing heart disease. I recommend it to\n"
        "all of my patients.\" — Dr. Alan Reeves\n\n"
        "Clinical-grade fish oil. Guaranteed to reduce your risk of a heart attack or your money back.\n"
        "Thousands of Americans have already made the switch. Order today at cardiopure.example.\n"
    ),
    "weightloss_testimonial.txt": (
        "SlimShift changed my life! \U0001f389\n\n"
        "\"I lost 30 pounds in one month without changing my diet or exercising at all. SlimShift melts\n"
        "the fat right off. Everyone who takes it gets these kinds of results!\" — Jenna M., verified customer\n\n"
        "The #1 fat burner in America. Take two capsules a day and watch the weight disappear.\n"
        "Results guaranteed. Start your transformation at slimshift.example.\n"
    ),
    "compliant_disclosed_ad.txt": (
        "#ad — paid partnership with BrightSmile\n\n"
        "I've been whitening with the BrightSmile kit for a month and I'm happy with how my teeth look.\n"
        "In a company-sponsored study, participants saw an average of two shades of improvement after four\n"
        "weeks; individual results vary. This is my honest opinion as a paid partner.\n\n"
        "If you'd like to try it, BrightSmile is available at brightsmile.example.\n"
    ),
}

MANIFEST = {
    "note": "SYNTHETIC ad samples authored for compliance-module smoke tests (CC-0). Not real advertisements; "
            "fictional brands/people (*.example domains). Each deliberately exercises specific 16 CFR 255 rules "
            "so CC-3 (claim extraction) and CC-6 (compliance_check) have testable inputs with a known expected "
            "signal. Real graded gold comes from NAD/FTC decisions in rung 2 (C-7).",
    "standard": "FTC 16 CFR Part 255 (Endorsement Guides)",
    "samples": [
        {"file": "influencer_skincare_no_disclosure.txt", "expected_signal": "violation",
         "targets": ["255.5 (undisclosed material connection: code GLOW20, no #ad)",
                     "255.1 (unsubstantiated efficacy: 'clinically proven to erase deep wrinkles in 7 days')"],
         "claim_types": ["endorsement", "efficacy", "health"]},
        {"file": "supplement_expert_endorsement.txt", "expected_signal": "violation",
         "targets": ["255.3 (expert endorsement without competent/reliable basis)",
                     "255.1 (unsubstantiated disease claim: 'reduce your risk of a heart attack')"],
         "claim_types": ["endorsement", "health", "guarantee"]},
        {"file": "weightloss_testimonial.txt", "expected_signal": "violation",
         "targets": ["255.2 (atypical results implied as typical, no clear-and-conspicuous disclosure)",
                     "255.1 (unsubstantiated: '30 pounds in one month without diet or exercise')"],
         "claim_types": ["endorsement", "efficacy", "guarantee"]},
        {"file": "compliant_disclosed_ad.txt", "expected_signal": "compliant",
         "targets": ["255.5 (material connection clearly disclosed: '#ad — paid partnership')",
                     "255.2 (qualified, substantiated claim with 'individual results vary')"],
         "claim_types": ["endorsement", "efficacy"]},
    ],
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, text in SAMPLES.items():
        (OUT / name).write_text(text, encoding="utf-8")
    (OUT / "manifest.json").write_text(json.dumps(MANIFEST, indent=2), encoding="utf-8")
    print(f"[fixtures] wrote {len(SAMPLES)} ad samples + manifest -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
