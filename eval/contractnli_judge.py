"""C-6 (compliance module de-risking): the judgment node on ContractNLI (document-grounded NLI).

Proves the LLM-via-seam judgment node can do document-grounded 3-way entailment
(entailment / contradiction / neutral) -- the core of the compliance verdict
(compliant / violation / not-addressed) -- on LABELED data, BEFORE we build any domain (ad-claims) gold.
Cheap: a balanced 150-pair slice (`data/eval/contractnli_slice.jsonl`), one structured call per pair,
concurrent. Reports accuracy + per-class recall + confusion (the GATE-bar signal for the judgment node).

  JUDGE_MODEL=deepseek/deepseek-v4-flash uv run python -m eval.contractnli_judge
"""

from __future__ import annotations

import collections
import json
import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel

from rag_wright.models.seam import build_structured
from rag_wright.util.concurrent import map_concurrent

SLICE = Path(os.environ.get("CNLI_SLICE", "data/eval/contractnli_slice.jsonl"))
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "deepseek/deepseek-v4-flash")
LABELS = ("entailment", "contradiction", "neutral")


class NLIVerdict(BaseModel):
    """The judgment-node output (verdict + rationale); the compliance analog of compliant/violation/not-addressed."""

    label: Literal["entailment", "contradiction", "neutral"]
    rationale: str = ""


_PROMPT = (
    "You check whether a HYPOTHESIS holds given the PREMISE (an excerpt from a contract). Decide EXACTLY one:\n"
    "- entailment: the premise supports / entails the hypothesis.\n"
    "- contradiction: the premise contradicts the hypothesis.\n"
    "- neutral: the premise neither supports nor contradicts it (not addressed / cannot tell).\n"
    "Default to neutral when genuinely unsure.\n\nPREMISE:\n{premise}\n\nHYPOTHESIS:\n{hypothesis}"
)


def _norm(label: str) -> str:
    """Normalize a model label to the closed set; anything unrecognized -> neutral (conservative default)."""
    lab = (label or "").strip().lower()
    return lab if lab in LABELS else "neutral"


def main() -> None:
    load_dotenv()
    rows = [json.loads(line) for line in SLICE.read_text(encoding="utf-8").splitlines() if line.strip()]
    run = build_structured(JUDGE_MODEL, NLIVerdict)

    def _judge(r):
        try:
            v = run.invoke(_PROMPT.format(premise=r["premise"], hypothesis=r["hypothesis"]))
            return _norm(v.label if v else "neutral")
        except Exception:  # noqa: BLE001 - a failed judgment defaults to the conservative label
            return "neutral"

    print(f"[c-6] judging {len(rows)} pairs via {JUDGE_MODEL}", flush=True)
    preds = map_concurrent(rows, _judge, max_concurrency=8, label="[c-6]", echo=True)
    gold = [r["gold"] for r in rows]
    acc = sum(p == g for p, g in zip(preds, gold)) / len(rows)
    conf = collections.Counter((g, p) for g, p in zip(gold, preds))

    print(f"\n=== C-6 ContractNLI judgment node  n={len(rows)}  model={JUDGE_MODEL} ===", flush=True)
    print(f"  accuracy = {acc:.3f}", flush=True)
    for lab in LABELS:
        n = sum(1 for g in gold if g == lab)
        rec = (sum(1 for g, p in zip(gold, preds) if g == lab and p == lab) / n) if n else 0.0
        print(f"  recall[{lab:13s}] = {rec:.3f}  (n={n})", flush=True)
    print("  confusion (gold -> pred):", flush=True)
    for g in LABELS:
        print(f"    {g:13s} -> {{" + ", ".join(f'{p}:{conf.get((g, p), 0)}' for p in LABELS) + "}", flush=True)


if __name__ == "__main__":
    main()
