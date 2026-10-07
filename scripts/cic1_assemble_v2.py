"""CIC-1c data assembly v2 (the DS loop). Builds a BIG, balanced TRAIN and a CLEANED, section-disjoint REAL TEST:

TRAIN (train-only sources): real train spans + CODE-ACCORD real rule sentences (CC-BY, positives) + real negatives
MINED from non-operative sections + teacher-generated examples (generated.jsonl) + targeted hard negatives
(hard_negatives.jsonl). TEST: real in-corpus FTC/OSHA spans in held-out sections, with documented label cleaning.

Cleaning: 4 unambiguous teacher mislabels (descriptive/illustrative sentences labeled 'rule') flipped to 'non',
keyed by text prefix and logged. Conservative -- genuinely-ambiguous 'may be liable' provisions left as-is."""
from __future__ import annotations

import json
import random
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

from rag_wright.packs.compliance.capabilities.requirement_extraction import operative_rule_spans
from rag_wright.packs.compliance.subgraphs.compliance_ingestion import is_operative
from rag_wright.packs.contracts.spans.segment import segment_clause

D = Path("data/compliance/cic1_labels")
CODE_ACCORD = Path("/private/tmp/claude-501/-Users-farhan-work-RAG-Wright/46292cd1-831b-4882-9a22-4954123663a0/scratchpad/CODE-ACCORD/annotated_data/entities/all.csv")
CORPORA = [("FTC 16 CFR 255", "data/compliance/ftc_16cfr255/16cfr255.sections.json"),
           ("FTC 16 CFR 233", "data/compliance/ftc_16cfr233/ftc_16cfr233.sections.json"),
           ("OSHA 29 CFR 1904", "data/compliance/osha_29cfr1904/osha_29cfr1904.sections.json")]
SEED = 13
TARGET_TEST_PER_CLASS = 24   # hold out whole sections until BOTH classes reach this in the real test (then symmetric)
CAP_CODE_ACCORD = 500   # real positive rules (cap so they don't swamp)
CAP_MINED_NEG = 160     # real easy negatives from non-operative sections

# Documented test-label cleaning: descriptive/illustrative sentences the teacher mislabeled 'rule' -> 'non'.
CLEAN_TO_NON = [
    "The adequacy of the disclosure will be evaluated from the perspective of the microtargeted",
    "Because the advertisement is targeted at older consumers, whether the disclosure is clear and conspicuous will be evaluated",
    "An advertiser who claims that an item has been",          # "Reduced to $9.99" illustrative example
    "Depending upon the language of the commercial, however, the audience may believe",
]


def _clean(text: str, label: str) -> str:
    for pre in CLEAN_TO_NON:
        if text.strip().startswith(pre):
            return "non"
    return label


def main() -> None:
    rng = random.Random(SEED)
    src = D / "spans_v2.jsonl" if (D / "spans_v2.jsonl").exists() else D / "spans.jsonl"
    print(f"using real-span source: {src.name}")
    rows = [json.loads(l) for l in src.read_text().splitlines() if l.strip()]
    ok = [r for r in rows if r.get("ok")]
    flips = 0
    by_sec = defaultdict(list)
    for r in ok:
        lab = "rule" if r["is_rule"] else "non"
        clean = _clean(r["text"], lab)
        if clean != lab:
            flips += 1
            print(f"  CLEAN rule->non: {r['text'][:80]!r}")
        by_sec[(r["source"], r["section"])].append({"text": r["text"], "label": clean,
                                                     "source": "real", "section": r["section"]})
    print(f"applied {flips} test-label cleanings (rule->non)")

    # section-disjoint REAL test: add whole sections (shuffled) until BOTH classes reach the target -> balanced + bigger
    secs = list(by_sec)
    rng.shuffle(secs)
    test_secs, tr_c, tn_c = set(), 0, 0
    for k in secs:
        if tr_c >= TARGET_TEST_PER_CLASS and tn_c >= TARGET_TEST_PER_CLASS:
            break
        test_secs.add(k)
        tr_c += sum(1 for x in by_sec[k] if x["label"] == "rule")
        tn_c += sum(1 for x in by_sec[k] if x["label"] == "non")

    test = [x for k in test_secs for x in by_sec[k]]
    train = [x for k, v in by_sec.items() if k not in test_secs for x in v]

    # --- TRAIN-ONLY augmentation ---
    # 1) CODE-ACCORD real rule sentences (positives, CC-BY)
    ca = pd.read_csv(CODE_ACCORD)["content"].dropna().astype(str).map(str.strip).unique().tolist()
    ca = [c for c in ca if 20 <= len(c) <= 400]
    rng.shuffle(ca)
    train += [{"text": c, "label": "rule", "source": "code-accord", "section": ""} for c in ca[:CAP_CODE_ACCORD]]
    # 2) real NEGATIVES mined from non-operative sections (no deontic cue -> definitions/scope/descriptive)
    mined = []
    for source, path in CORPORA:
        for sec in json.loads(Path(path).read_text()):
            txt = (sec.get("text") or "").strip()
            if not txt or is_operative(txt):
                continue  # only NON-operative sections
            for sp in segment_clause("", txt):
                t = sp.text.strip()
                if 20 <= len(t) <= 400:
                    mined.append({"text": t, "label": "non", "source": "mined-neg", "section": sec["section"]})
    rng.shuffle(mined)
    train += mined[:CAP_MINED_NEG]
    # 3) teacher-generated (balanced) + 4) targeted hard negatives
    for f, src in [("generated.jsonl", "gen"), ("hard_negatives.jsonl", "hardneg")]:
        p = D / f
        if p.exists():
            for l in p.read_text().splitlines():
                if l.strip():
                    g = json.loads(l)
                    train.append({"text": g["text"], "label": g["label"], "source": src, "section": g.get("category", "")})

    # symmetric test
    t_rule = [r for r in test if r["label"] == "rule"]; t_non = [r for r in test if r["label"] == "non"]
    rng.shuffle(t_rule); rng.shuffle(t_non); m = min(len(t_rule), len(t_non))
    test = t_rule[:m] + t_non[:m]
    # balance train (cap each class to the min)
    tr_rule = [r for r in train if r["label"] == "rule"]; tr_non = [r for r in train if r["label"] == "non"]
    rng.shuffle(tr_rule); rng.shuffle(tr_non); cap = min(len(tr_rule), len(tr_non))
    train = tr_rule[:cap] + tr_non[:cap]; rng.shuffle(train)

    (D / "operative_train.jsonl").write_text("".join(json.dumps(r) + "\n" for r in train))
    (D / "operative_test.jsonl").write_text("".join(json.dumps(r) + "\n" for r in test))
    print(f"TRAIN n={len(train)} by_source={dict(Counter(r['source'] for r in train))} "
          f"labels={dict(Counter(r['label'] for r in train))}")
    print(f"TEST  n={len(test)} labels={dict(Counter(r['label'] for r in test))} (symmetric, real, cleaned)")


if __name__ == "__main__":
    main()
