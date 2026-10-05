"""CIC-1c hybrid test: SetFit for CONFIDENT predictions, Qwen-on-OpenRouter (the rubric) for LOW-confidence ones.
Measures hybrid accuracy on the reliable gold vs the classifier alone, and the LLM-call cost (only the uncertain
subset -> cheap). OpenRouter (profile qwen3.8-27b-or), NOT the Modal server."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv("/Users/farhan/work/RAG_Wright/.env")

from pydantic import BaseModel, Field  # noqa: E402

from rag_wright.models.seam import build_structured  # noqa: E402

PROBA = Path("data/compliance/cic1_labels/proba.json")
MODEL_ID = "qwen3.8-27b-or"  # OpenRouter -> qwen/qwen3.8-27b
THRESHOLDS = [0.7, 0.8, 0.9, 0.95, 0.99]
QUERY_BELOW = 0.99  # query the LLM once for every item below the max threshold, then reuse across thresholds

RUBRIC = """Does this regulatory sentence, BY ITSELF, state a BINDING operative rule (an obligation/prohibition/
permission, a binding liability or conditional consequence)? Answer is_rule=true for that. Answer is_rule=false for
an illustrative EXAMPLE (even with a modal), a definition, a scope/purpose line, a cross-reference, a negated
requirement, or a descriptive statement.

Sentence:
\"\"\"{t}\"\"\"
"""


class OperativeLabel(BaseModel):
    is_rule: bool = Field(description="True iff a binding operative rule per the rubric.")
    reason: str = Field(default="")


def conf(r):
    return max(r["proba_rule"], 1 - r["proba_rule"])


async def llm_label(runnable, text, sem):
    async with sem:
        try:
            out = await runnable.ainvoke(RUBRIC.format(t=text))
            return "rule" if out.is_rule else "non"
        except Exception as exc:  # noqa: BLE001
            return f"ERR:{str(exc)[:60]}"


async def main():
    data = json.load(open(PROBA))
    runnable = build_structured(MODEL_ID, OperativeLabel)
    to_query = [r for r in data if conf(r) < QUERY_BELOW]
    print(f"gold n={len(data)}; LLM-queried (conf<{QUERY_BELOW}) = {len(to_query)}", flush=True)
    sem = asyncio.Semaphore(8)
    llm = dict(zip((r["text"] for r in to_query),
                   await asyncio.gather(*(llm_label(runnable, r["text"], sem) for r in to_query))))

    def acc(preds):
        return sum(p == r["gold"] for p, r in zip(preds, data)) / len(data)

    base = acc([r["pred"] for r in data])
    print(f"\nclassifier-only accuracy: {base:.3f}")
    print(f"{'thr':>5} {'routed':>7} {'hybrid_acc':>11} {'rule_rec':>9} {'non_rec':>8}")
    for thr in THRESHOLDS:
        preds, routed = [], 0
        for r in data:
            if conf(r) >= thr:
                preds.append(r["pred"])
            else:
                routed += 1
                lab = llm.get(r["text"], r["pred"])
                preds.append(r["pred"] if lab.startswith("ERR") else lab)
        gold = [r["gold"] for r in data]
        rr = sum(p == "rule" for p, g in zip(preds, gold) if g == "rule") / sum(g == "rule" for g in gold)
        nr = sum(p == "non" for p, g in zip(preds, gold) if g == "non") / sum(g == "non" for g in gold)
        print(f"{thr:>5} {routed:>7} {acc(preds):>11.3f} {rr:>9.2f} {nr:>8.2f}", flush=True)

    # how good is the LLM on the routed (uncertain) items alone?
    ur = [(llm[r["text"]], r["gold"]) for r in to_query if not llm[r["text"]].startswith("ERR")]
    if ur:
        print(f"\nLLM accuracy on the {len(ur)} uncertain items: {sum(p==g for p,g in ur)/len(ur):.3f}")
        print(f"classifier accuracy on those same items: {sum(r['pred']==r['gold'] for r in to_query)/len(to_query):.3f}")


if __name__ == "__main__":
    asyncio.run(main())
