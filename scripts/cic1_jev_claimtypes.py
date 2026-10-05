"""CIC: test Jev (TypeSafe System-One decision model, via OpenRouter Decisions API) on the claim_types multi-label
field. Multi-label -> 8 `noul` (yes/no) questions per rule in ONE Jev call, each returning P(yes). Reference =
the current LLM (Qwen teacher) claim_types labels on the real FTC rules, i.e. "can Jev replace the LLM for this
field?" (teacher-silver reference; a first-look agreement/accuracy read). Cheap: Jev is $0.042/M input, output free."""
from __future__ import annotations

import asyncio
import json
import os
from collections import Counter
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv("/Users/farhan/work/RAG_Wright/.env")

ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
MODEL = "typesafe/jev-1.13"
SPANS = Path("data/compliance/cic1_labels/spans.jsonl")

# the 8 FTC claim types. (b) efficacy vs performance SHARPENED to disambiguate the overlap seen at 0.5.
CLAIM_TYPES = {
    "efficacy": "claims that the product WORKS / is EFFECTIVE at its intended purpose (cures, treats, achieves the promised outcome). NOT mere speed/durability/quantity claims.",
    "comparative": "claims COMPARING the product to competitors or alternatives (better than, #1, versus)",
    "pricing": "claims about PRICE, discount, 'reduced', 'free', 2-for-1, or savings",
    "health": "claims about HEALTH, medical, disease, nutrition, or bodily safety",
    "environmental": "claims that are ENVIRONMENTAL / 'green' / eco / sustainable / biodegradable",
    "endorsement": "rules about ENDORSEMENTS, testimonials, reviews, or who may endorse",
    "performance": "claims about HOW WELL the product performs on MEASURABLE attributes — speed, durability, strength, capacity, output level. NOT merely that it works (that is efficacy).",
    "guarantee": "claims of a GUARANTEE, warranty, or money-back promise",
}


def build_questions():
    return {ct: {"type": "noul",
                 "instructions": f"Does this regulatory rule apply to {ct} advertising claims?",
                 "criteria": {"true": desc, "false": f"the rule does NOT specifically concern {ct} claims"}}
            for ct, desc in CLAIM_TYPES.items()}


FEWSHOT = ("Guidance examples (sentence -> applicable claim types), refer to them:\n"
           "- 'This supplement cures insomnia' -> efficacy, health\n"
           "- 'Cleans 3x faster and lasts longer than Brand X' -> performance, comparative\n"
           "- 'Now reduced to $9.99, was $20' -> pricing\n"
           "- '100% recyclable, carbon-neutral packaging' -> environmental\n"
           "- 'As recommended by Dr. Smith in this testimonial' -> endorsement\n"
           "- 'Guaranteed or your money back' -> guarantee\n"
           "(efficacy = it works/achieves the result; performance = measurable how-well: speed/durability/strength)\n\n"
           "Sentence to classify:\n")


async def jev(client, state, sem):
    async with sem:
        r = await client.post(ENDPOINT, headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"},
                              json={"model": MODEL, "state": state, "questions": build_questions()}, timeout=60)
        r.raise_for_status()
        d = r.json()
        return {ct: float(a.get("noul", 0.0)) for ct, a in d["answers"].items()}, d.get("usage", {}).get("cost", 0.0)


def _micro(preds, gold):
    tp = sum(len(p & g) for p, g in zip(preds, gold)); fp = sum(len(p - g) for p, g in zip(preds, gold))
    fn = sum(len(g - p) for p, g in zip(preds, gold))
    pr = tp / (tp + fp) if tp + fp else 0.0; re = tp / (tp + fn) if tp + fn else 0.0
    return pr, re, (2 * pr * re / (pr + re) if pr + re else 0.0), sum(p == g for p, g in zip(preds, gold)) / len(gold)


async def main():
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument("--fewshot", action="store_true"); A = ap.parse_args()
    rows = [json.loads(l) for l in SPANS.read_text().splitlines() if l.strip()]
    ftc = [r for r in rows if r.get("ok") and r.get("is_rule") and "FTC" in r["source"]]
    gold = [{c.strip().lower() for c in (r.get("claim_types") or []) if c.strip().lower() in CLAIM_TYPES} for r in ftc]
    n = len(ftc)
    print(f"Jev claim_types test (fewshot={A.fewshot}): {n} FTC rules; reference = LLM(teacher) labels", flush=True)
    sem = asyncio.Semaphore(8)
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*(jev(client, (FEWSHOT + r["text"]) if A.fewshot else r["text"], sem) for r in ftc))
    probs = [p for p, _ in results]; cost = sum(c for _, c in results)

    # (a) threshold sweep
    print("\n(a) noul-threshold sweep (sharpened efficacy/performance criteria):")
    print(f"{'thr':>5}{'prec':>7}{'recall':>8}{'microF1':>9}{'exact':>7}")
    best = None
    for thr in [0.5, 0.6, 0.7, 0.8, 0.9]:
        preds = [{ct for ct, v in p.items() if v >= thr} for p in probs]
        pr, re, f1, ex = _micro(preds, gold)
        print(f"{thr:>5}{pr:>7.2f}{re:>8.2f}{f1:>9.3f}{ex:>7.2f}")
        if best is None or f1 > best[1]:
            best = (thr, f1, preds)
    thr, f1, preds = best
    print(f"\nbest threshold = {thr} (microF1={f1:.3f}); per-label F1 there:")
    for ct in CLAIM_TYPES:
        g = [ct in x for x in gold]; p = [ct in x for x in preds]
        t = sum(a and b for a, b in zip(p, g)); fpl = sum(a and not b for a, b in zip(p, g)); fnl = sum((not a) and b for a, b in zip(p, g))
        pr = t / (t + fpl) if t + fpl else 0.0; re = t / (t + fnl) if t + fnl else 0.0
        fl = 2 * pr * re / (pr + re) if pr + re else 0.0
        print(f"  {ct:<14} gold_pos={sum(g):<3} jev_pos={sum(p):<3} F1={fl:.2f}")
    print(f"\ntotal Jev cost: ${cost:.5f}  ({n} calls)")


if __name__ == "__main__":
    asyncio.run(main())
