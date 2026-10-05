"""CIC-1c prep: build a SECTION-DISJOINT, symmetric train/test split for the operative-rule binary classifier
from the Modal-Qwen silver labels (data/compliance/cic1_labels/spans.jsonl).

setfit Phase 1 discipline: split by the leakage unit (source+section), NOT by row, so near-duplicate spans from one
section cannot straddle train/test; symmetric test (equal per class) for comparable per-class numbers; scarce class
= 'non' (50). Labels: 'rule' (is_rule true) | 'non' (is_rule false)."""
from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path

SRC = Path("data/compliance/cic1_labels/spans.jsonl")
GEN = Path("data/compliance/cic1_labels/generated.jsonl")  # teacher-generated, TRAIN-ONLY silver (setfit Phase 4)
OUT = Path("data/compliance/cic1_labels")
SEED = 13
TARGET_TEST_NON = 22  # hold out whole REAL sections until the scarce class reaches this in test (bigger = less noisy)
TRAIN_RULE_CAP_RATIO = 1  # near-balance in train (generated supplies plenty of both classes)


def main() -> None:
    rows = [json.loads(l) for l in SRC.open()]
    ok = [r for r in rows if r.get("ok")]
    by_sec: dict[tuple, list] = defaultdict(list)
    for r in ok:
        by_sec[(r["source"], r["section"])].append(r)

    non_secs = [k for k, v in by_sec.items() if any(not r["is_rule"] for r in v)]
    random.Random(SEED).shuffle(non_secs)

    test_secs: set = set()
    test_non = 0
    for k in non_secs:
        if test_non >= TARGET_TEST_NON:
            break
        test_secs.add(k)
        test_non += sum(1 for r in by_sec[k] if not r["is_rule"])

    def rec(r):  # the trained model sees text -> label only
        return {"text": r["text"], "label": "rule" if r["is_rule"] else "non",
                "source": r["source"], "section": r["section"]}

    test = [rec(r) for k in test_secs for r in by_sec[k]]  # REAL, in-corpus (the honest transfer test)
    train = [rec(r) for k, v in by_sec.items() if k not in test_secs for r in v]  # REAL train spans

    # setfit Phase 4: ADD the teacher-generated examples to TRAIN ONLY (never test). Marked source=generated.
    if GEN.exists():
        for l in GEN.read_text().splitlines():
            if l.strip():
                g = json.loads(l)
                train.append({"text": g["text"], "label": g["label"], "source": "generated", "section": g.get("category", "")})

    # symmetric test: equal per class (drop the surplus of the larger class, seeded)
    rng = random.Random(SEED)
    t_rule = [r for r in test if r["label"] == "rule"]
    t_non = [r for r in test if r["label"] == "non"]
    m = min(len(t_rule), len(t_non))
    rng.shuffle(t_rule); rng.shuffle(t_non)
    test = t_rule[:m] + t_non[:m]

    # train balance: cap 'rule' at RATIO x 'non' (keep near-balance; generated supplies plenty)
    tr_rule = [r for r in train if r["label"] == "rule"]
    tr_non = [r for r in train if r["label"] == "non"]
    rng.shuffle(tr_rule); rng.shuffle(tr_non)
    cap = TRAIN_RULE_CAP_RATIO * min(len(tr_rule), len(tr_non))
    train = tr_rule[:cap] + tr_non[:cap]
    rng.shuffle(train)

    (OUT / "operative_train.jsonl").write_text("".join(json.dumps(r) + "\n" for r in train))
    (OUT / "operative_test.jsonl").write_text("".join(json.dumps(r) + "\n" for r in test))

    def dist(x):
        return {"rule": sum(l["label"] == "rule" for l in x), "non": sum(l["label"] == "non" for l in x)}

    print(f"test sections (held out, disjoint): {sorted(f'{s}:{n}' for s, n in test_secs)}")
    print(f"TRAIN {dist(train)}  n={len(train)}")
    print(f"TEST  {dist(test)}  n={len(test)}  (symmetric)")
    print(f"wrote {OUT}/operative_train.jsonl + operative_test.jsonl")


if __name__ == "__main__":
    main()
