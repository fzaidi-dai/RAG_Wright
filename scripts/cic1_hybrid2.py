"""CIC-1c hybrid v2: (A) temperature-calibrate the SetFit confidence and SHOW routing is invariant (monotonic);
(B) a better uncertainty signal -- ENSEMBLE DISAGREEMENT (bge vs minilm) + averaged-proba MARGIN -- routed to
Qwen-on-OpenRouter. Finds the minimal-LLM config that clears 0.9 on the reliable gold."""
from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path

from dotenv import load_dotenv

load_dotenv("/Users/farhan/work/RAG_Wright/.env")

from pydantic import BaseModel, Field  # noqa: E402

from rag_wright.models.seam import build_structured  # noqa: E402

D = Path("data/compliance/cic1_labels")
MODEL_ID = "qwen3.8-27b-or"
RUBRIC = ("Does this regulatory sentence, BY ITSELF, state a BINDING operative rule (obligation/prohibition/"
          "permission, or a binding liability/conditional consequence)? is_rule=true for that. is_rule=false for an "
          "illustrative EXAMPLE (even with a modal), a definition, scope/purpose, a cross-reference, a negated "
          "requirement, or a descriptive statement.\n\nSentence:\n\"\"\"{t}\"\"\"")


class L(BaseModel):
    is_rule: bool = Field(description="True iff a binding operative rule.")
    reason: str = Field(default="")


def temperature_fit(ps, ys, iters=300, lr=0.05):
    """Fit a scalar temperature T on logit(p_rule) by NLL (ys: 1=rule,0=non). Monotonic -> preserves ranking."""
    import statistics
    logits = [math.log(min(max(p, 1e-6), 1 - 1e-6) / (1 - min(max(p, 1e-6), 1 - 1e-6))) for p in ps]
    T = 1.0
    for _ in range(iters):
        g = 0.0
        for z, y in zip(logits, ys):
            q = 1 / (1 + math.exp(-z / T))
            g += (q - y) * (-z / (T * T))
        T -= lr * g / len(ys)
        T = max(T, 0.05)
    return T


async def main():
    bge = {r["text"]: r for r in json.load(open(D / "proba.json"))}
    mini = {r["text"]: r for r in json.load(open(D / "proba_minilm.json"))}
    items = [bge[t] for t in bge if t in mini]
    gold = {r["text"]: r["gold"] for r in items}
    ps = [r["proba_rule"] for r in items]
    ys = [1 if gold[r["text"]] == "rule" else 0 for r in items]

    # (A) temperature calibration + invariance demonstration
    T = temperature_fit(ps, ys)
    def cal(p): z = math.log(min(max(p,1e-6),1-1e-6)/(1-min(max(p,1e-6),1-1e-6))); q = 1/(1+math.exp(-z/T)); return q
    raw_conf = sorted(range(len(items)), key=lambda i: max(ps[i], 1 - ps[i]))
    cal_conf = sorted(range(len(items)), key=lambda i: max(cal(ps[i]), 1 - cal(ps[i])))
    print(f"(A) fitted temperature T={T:.2f}; raw-vs-calibrated confidence ranking identical: {raw_conf == cal_conf}")
    print("    -> monotonic: calibration makes thresholds interpretable but does NOT change which items route.\n")

    # LLM-label every item once (reused across routers); OpenRouter
    runnable = build_structured(MODEL_ID, L)
    sem = asyncio.Semaphore(8)
    async def lab(t):
        async with sem:
            try:
                return "rule" if (await runnable.ainvoke(RUBRIC.format(t=t))).is_rule else "non"
            except Exception as e:  # noqa: BLE001
                return "ERR"
    llm = dict(zip((r["text"] for r in items), await asyncio.gather(*(lab(r["text"]) for r in items))))

    def evaluate(route_flags, base_pred):
        preds = [llm[items[i]["text"]] if route_flags[i] and not llm[items[i]["text"]].startswith("ERR")
                 else base_pred[i] for i in range(len(items))]
        acc = sum(preds[i] == gold[items[i]["text"]] for i in range(len(items))) / len(items)
        return acc, sum(route_flags)

    bge_pred = ["rule" if ps[i] >= 0.5 else "non" for i in range(len(items))]
    mini_pred = ["rule" if mini[items[i]["text"]]["proba_rule"] >= 0.5 else "non" for i in range(len(items))]
    avg = [(ps[i] + mini[items[i]["text"]]["proba_rule"]) / 2 for i in range(len(items))]
    avg_pred = ["rule" if a >= 0.5 else "non" for a in avg]

    print(f"bge-only acc={evaluate([False]*len(items), bge_pred)[0]:.3f}  "
          f"ensemble(avg)-only acc={evaluate([False]*len(items), avg_pred)[0]:.3f}  "
          f"LLM-all acc={evaluate([True]*len(items), avg_pred)[0]:.3f}")

    print("\n(B1) ROUTER = ensemble disagreement (bge_pred != minilm_pred):")
    dis = [bge_pred[i] != mini_pred[i] for i in range(len(items))]
    a, n = evaluate(dis, avg_pred); print(f"   routed={n}/{len(items)} ({100*n/len(items):.0f}%)  hybrid_acc={a:.3f}")

    print("\n(B2) ROUTER = averaged-proba margin |avg-0.5| < m:")
    for m in [0.1, 0.15, 0.2, 0.25, 0.3]:
        flags = [abs(avg[i] - 0.5) < m for i in range(len(items))]
        a, n = evaluate(flags, avg_pred); print(f"   m={m}: routed={n}/{len(items)} ({100*n/len(items):.0f}%)  hybrid_acc={a:.3f}")

    print("\n(B3) ROUTER = disagreement OR margin<0.2 (union):")
    flags = [dis[i] or abs(avg[i] - 0.5) < 0.2 for i in range(len(items))]
    a, n = evaluate(flags, avg_pred); print(f"   routed={n}/{len(items)} ({100*n/len(items):.0f}%)  hybrid_acc={a:.3f}")


if __name__ == "__main__":
    asyncio.run(main())
