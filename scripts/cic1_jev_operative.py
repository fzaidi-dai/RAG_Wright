"""CIC-1c (c): test Jev on the OPERATIVE-RULE gate -- the binary decision where SetFit capped at 0.82 and couldn't
be hybridized (confident/unroutable errors). Jev: ONE `noul` per span with the rubric, on the RELIABLE consensus
gold (50). Reports accuracy + per-class + whether Jev's CALIBRATED prob routes cleanly (unlike SetFit). Compare:
SetFit 0.82, LLM-rubric 0.92."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv("/Users/farhan/work/RAG_Wright/.env")

ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
MODEL = "typesafe/jev-1.13"
GOLD = Path("data/compliance/cic1_labels/operative_test.jsonl")
Q = {"operative": {"type": "noul",
     "instructions": "Does this regulatory sentence, BY ITSELF, state a BINDING operative rule?",
     "criteria": {
        "true": "it directly imposes or grants a binding requirement -- an obligation (must/shall/required), a "
                "prohibition (may not/shall not), a permission/allowance (may), or a binding liability or "
                "conditional consequence ('X is deceptive if ...', 'X may be liable ...')",
        "false": "it does NOT itself bind: an illustrative EXAMPLE even with a modal ('For instance, a seller may "
                 "not ...'), a definition, a scope/purpose statement, a cross-reference ('See § ...'), a NEGATED "
                 "requirement ('no disclosure is required'), or a descriptive/explanatory remark"}}}


FEWSHOT = ("Guidance examples (sentence -> is_rule), refer to them:\n"
           "[rule] Advertisers must disclose any material connection with an endorser. (obligation)\n"
           "[rule] An endorser may not misrepresent their experience. (prohibition)\n"
           "[rule] An advertiser may be liable for a deceptive endorsement. (liability = binding consequence)\n"
           "[non] For instance, a manufacturer may not affix inflated price tickets. (illustrative example)\n"
           "[non] (See § 255.3 regarding the evaluation an expert must conduct.) (cross-reference)\n"
           "[non] No disclosure is required because no claim is made. (negated requirement)\n\n"
           "Sentence to classify:\n")


async def jev(client, state, sem):
    async with sem:
        r = await client.post(ENDPOINT, headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"},
                              json={"model": MODEL, "state": state, "questions": Q}, timeout=60)
        r.raise_for_status()
        d = r.json()
        return float(d["answers"]["operative"]["noul"]), d.get("usage", {}).get("cost", 0.0)


async def main():
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument("--fewshot", action="store_true"); A = ap.parse_args()
    gold = [json.loads(l) for l in GOLD.read_text().splitlines() if l.strip()]
    n = len(gold)
    sem = asyncio.Semaphore(8)
    async with httpx.AsyncClient() as client:
        res = await asyncio.gather(*(jev(client, (FEWSHOT + r["text"]) if A.fewshot else r["text"], sem) for r in gold))
    probs = [p for p, _ in res]; cost = sum(c for _, c in res)
    preds = ["rule" if p >= 0.5 else "non" for p in probs]
    g = [r["label"] for r in gold]
    acc = sum(a == b for a, b in zip(preds, g)) / n
    rr = sum(a == "rule" for a, b in zip(preds, g) if b == "rule") / sum(b == "rule" for b in g)
    nr = sum(a == "non" for a, b in zip(preds, g) if b == "non") / sum(b == "non" for b in g)
    print(f"Jev operative-rule on reliable gold (n={n}): acc={acc:.3f} rule_rec={rr:.2f} non_rec={nr:.2f}")
    print(f"  (compare: SetFit 0.82, LLM-rubric 0.92)")
    # calibration / routing: are Jev's errors near 0.5 (routable) or confident (like SetFit)?
    conf = lambda p: max(p, 1 - p)
    wrong = [(conf(probs[i]), probs[i], g[i], preds[i], gold[i]["text"]) for i in range(n) if preds[i] != g[i]]
    print(f"\n{len(wrong)} errors; their confidence (low = routable):")
    for c, p, gl, pr, t in sorted(wrong):
        print(f"  conf={c:.2f} P(rule)={p:.2f} gold={gl:<4} pred={pr:<4} :: {t[:80]}")
    # hybrid: route least-confident to the already-known LLM(0.92)? here just show how many errors are < given conf
    for thr in [0.6, 0.7, 0.8, 0.9]:
        routable = sum(1 for c, *_ in wrong if c < thr)
        print(f"  errors with conf<{thr}: {routable}/{len(wrong)} (would be caught by routing uncertain to LLM)")
    print(f"\ntotal Jev cost: ${cost:.5f} ({n} calls)")


if __name__ == "__main__":
    asyncio.run(main())
