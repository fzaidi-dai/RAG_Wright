"""CIC-1c assembly v3: a CONSENSUS-adjudicated real gold + a BALANCE-FIXED train.

GOLD (test): real FTC/OSHA spans where the TWO independent rubric passes (spans_v2, spans_v3) AGREE -> confident
label; disagreements are adjudicated by an explicit, documented map (ADJUDICATE below) -- my best-effort human
proxy, pending a legal expert. Reports the inter-pass agreement rate (label reliability).

TRAIN balance fix: the last round over-weighted modal-containing hard-negatives (model missed clear 'must'
obligations). Here negatives = real-non + generated-non + hard-neg CAPPED to <=~35%, and positives are anchored by
REAL human-labeled rules (CODE-ACCORD + EU deontic, both with shall/must) so modals appear in BOTH classes."""
from __future__ import annotations

import json
import random
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

D = Path("data/compliance/cic1_labels")
SP = Path("/private/tmp/claude-501/-Users-farhan-work-RAG-Wright/46292cd1-831b-4882-9a22-4954123663a0/scratchpad")
CODE_ACCORD = SP / "CODE-ACCORD/annotated_data/entities/all.csv"
EU_DEONTIC = SP / "eu_deontic/df_a1.csv"
SEED = 13
TARGET_TEST_PER_CLASS = 25
CAP_CODE_ACCORD = 300
CAP_EU = 210
CAP_HARDNEG = 150          # <= ~35% of negatives (fix over-correction)

# Adjudication of the 17 v2/v3 DISAGREEMENTS (my documented rubric calls; best-effort proxy pending a legal expert).
ADJUDICATE: dict[str, str] = {
    "The influencer and the advertiser may also be liable": "rule",   # liability consequence (conditional-binding)
    "Accordingly, the president's statement will be deemed": "non",    # analytical conclusion within an example
    "(a) Basic requirement. (1) If your company had 10 or fewer": "rule",  # exemption RULE
    "They may also be liable for their roles with respect": "rule",    # liability provision
    "The advertisement should thus clearly and conspicuously inform": "non",  # example-embedded application
    "(For the responsibilities of an endorser who is an expert, see": "rule",  # liability (cross-ref is parenthetical)
    "In such a case, entering 180 in the total days away": "non",      # example-embedded descriptive conclusion
    "(f) Advertising agencies, public relations firms, review brokers": "rule",  # enumerated subject of a liability rule
    "(e) Endorsers may be liable for statements made": "rule",         # liability provision
    "(3) May I adjust the current audiogram": "rule",                  # Q&A answer GRANTS a permission
    "You should not expect to have similar results": "rule",           # binding standard ("deceptive unless substantiated")
    "Sellers are not required to display customer reviews": "non",      # negated requirement (per rubric)
    "To determine whether this is the case, you must evaluate": "rule", # obligation ("you must evaluate")
    "You may contact your nearest OSHA office": "non",                  # advisory offer of help, not an operative norm
    "(4) Do I have to record the hearing loss if I am going to retest": "rule",  # conditional exemption
    "(vii) How do I handle vague restrictions from a physician": "non", # how-to Q&A
    "(ii) Rather than searching through a list of primary business": "non",  # procedural option / how-to
}


def main() -> None:
    rng = random.Random(SEED)
    v2 = {r["text"]: r for r in (json.loads(l) for l in (D / "spans_v2.jsonl").read_text().splitlines() if l.strip()) if r.get("ok")}
    v3 = {r["text"]: r for r in (json.loads(l) for l in (D / "spans_v3.jsonl").read_text().splitlines() if l.strip()) if r.get("ok")}
    common = [t for t in v2 if t in v3]
    agree = [t for t in common if v2[t]["is_rule"] == v3[t]["is_rule"]]
    disagree = [t for t in common if v2[t]["is_rule"] != v3[t]["is_rule"]]
    print(f"inter-pass agreement: {len(agree)}/{len(common)} = {len(agree)/max(len(common),1):.3f}  (disagreements={len(disagree)})")

    def gold_label(t: str) -> str | None:
        if v2[t]["is_rule"] == v3[t]["is_rule"]:
            return "rule" if v2[t]["is_rule"] else "non"
        for pre, lab in ADJUDICATE.items():
            if t.strip().startswith(pre):
                return lab
        return None  # unadjudicated disagreement -> excluded from gold (not silently guessed)

    by_sec = defaultdict(list)
    excluded = 0
    for t in common:
        g = gold_label(t)
        if g is None:
            excluded += 1
            continue
        by_sec[(v2[t]["source"], v2[t]["section"])].append({"text": t, "label": g, "source": "real", "section": v2[t]["section"]})
    if excluded:
        print(f"NOTE: {excluded} unadjudicated disagreements excluded from gold (fill ADJUDICATE to include)")

    # section-disjoint gold test (balanced)
    secs = list(by_sec); rng.shuffle(secs)
    test_secs, rc, nc = set(), 0, 0
    for k in secs:
        if rc >= TARGET_TEST_PER_CLASS and nc >= TARGET_TEST_PER_CLASS:
            break
        test_secs.add(k)
        rc += sum(1 for x in by_sec[k] if x["label"] == "rule"); nc += sum(1 for x in by_sec[k] if x["label"] == "non")
    test = [x for k in test_secs for x in by_sec[k]]
    train = [x for k, v in by_sec.items() if k not in test_secs for x in v]

    # positives: real human-labeled rules (anchors clear obligations) ---
    ca = [c for c in pd.read_csv(CODE_ACCORD)["content"].dropna().astype(str).map(str.strip).unique() if 20 <= len(c) <= 400]
    rng.shuffle(ca); train += [{"text": c, "label": "rule", "source": "code-accord", "section": ""} for c in ca[:CAP_CODE_ACCORD]]
    eu = pd.read_csv(EU_DEONTIC)
    eu_rules = [t.strip() for t in eu["text"].dropna().astype(str) if 20 <= len(t.strip()) <= 400]  # all EU rows are deontic -> rule
    rng.shuffle(eu_rules); train += [{"text": t, "label": "rule", "source": "eu-deontic", "section": ""} for t in eu_rules[:CAP_EU]]
    # generated (balanced) + hard-neg (CAPPED) + hard-pos (the rule sub-types the gold exposed)
    for f, src, cap in [("generated.jsonl", "gen", 10**9), ("hard_negatives.jsonl", "hardneg", CAP_HARDNEG),
                        ("hard_positives.jsonl", "hardpos", 10**9)]:
        p = D / f
        if p.exists():
            rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
            rng.shuffle(rows)
            train += [{"text": g["text"], "label": g["label"], "source": src, "section": g.get("category", "")} for g in rows[:cap]]

    # symmetric gold test
    tr = [r for r in test if r["label"] == "rule"]; tn = [r for r in test if r["label"] == "non"]
    rng.shuffle(tr); rng.shuffle(tn); m = min(len(tr), len(tn)); test = tr[:m] + tn[:m]
    # balanced train -- PRIORITIZE hard-pos + real in the positive mix (don't let CODE-ACCORD dilute the sub-types
    # the gold needs), and hard-neg + real in the negative mix.
    def prioritize(rows, keep):
        a = [r for r in rows if r["source"] in keep]; b = [r for r in rows if r["source"] not in keep]
        rng.shuffle(a); rng.shuffle(b); return a + b
    trr = prioritize([r for r in train if r["label"] == "rule"], ("hardpos", "real"))
    trn = prioritize([r for r in train if r["label"] == "non"], ("hardneg", "real"))
    cap = min(len(trr), len(trn)); train = trr[:cap] + trn[:cap]; rng.shuffle(train)

    (D / "operative_train.jsonl").write_text("".join(json.dumps(r) + "\n" for r in train))
    (D / "operative_test.jsonl").write_text("".join(json.dumps(r) + "\n" for r in test))
    neg_src = Counter(r["source"] for r in train if r["label"] == "non")
    print(f"TRAIN n={len(train)} labels={dict(Counter(r['label'] for r in train))} neg_sources={dict(neg_src)}")
    print(f"  hardneg share of negatives: {neg_src.get('hardneg',0)}/{sum(neg_src.values())}")
    print(f"TEST(gold) n={len(test)} labels={dict(Counter(r['label'] for r in test))} (consensus, section-disjoint)")


if __name__ == "__main__":
    main()
