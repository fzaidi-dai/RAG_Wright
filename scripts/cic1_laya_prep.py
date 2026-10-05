"""CIC-1c Laya prep: convert the operative-rule train/test (scripts/cic1_prep_operative.py output) into Laya's
{state, questions, gold} schema (dim='operative'), mirroring clause-classifier-ab/build_laya_data.py. Criteria are
the semantic-guidance lever. Plain variant (no few-shot) for the baseline. Generated stays train-only (already is,
via the prep); test is the real in-corpus held-out set."""
from __future__ import annotations

import json
from pathlib import Path

SRC = Path("data/compliance/cic1_labels")
OUT = SRC / "laya"
DIM = "operative"
KEYS = ["rule", "non"]
SPEC = {
    "type": "choice",
    "instructions": "Is this sentence a BINDING operative rule, or NOT a rule?",
    "criteria": {
        "rule": "a binding obligation, prohibition, or permission that imposes or grants a requirement on a party "
                "(must / shall / must not / may not / may / is required / is permitted)",
        "non": "NOT a binding rule: a definition, a descriptive or explanatory statement (even one using 'may' in "
               "an epistemic 'might' sense, e.g. 'connections may be immaterial'), a scope/purpose statement, or a "
               "cross-reference to another provision",
    },
}


def _case(text: str, gold: str) -> dict:
    return {"state": text,
            "questions": {DIM: SPEC},
            "gold": {DIM: {"probabilities": {k: (1.0 if k == gold else 0.0) for k in KEYS}, "label": gold}}}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for split in ("train", "test"):
        rows = [json.loads(l) for l in (SRC / f"operative_{split}.jsonl").read_text().splitlines() if l.strip()]
        cases = [_case(r["text"], r["label"]) for r in rows]
        (OUT / f"{split}.jsonl").write_text("\n".join(json.dumps(c) for c in cases))
        n_rule = sum(r["label"] == "rule" for r in rows)
        print(f"[laya-prep] {split}: {len(cases)} cases (rule={n_rule}, non={len(cases)-n_rule}) -> {OUT}/{split}.jsonl")


if __name__ == "__main__":
    main()
