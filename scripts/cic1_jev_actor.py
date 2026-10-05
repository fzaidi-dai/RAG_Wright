"""CIC: test Jev on the ACTOR field (single-label -> `choice` question). Canonicalize teacher actor labels via the
ttl actor-synonyms into a fixed role set; measure Jev choice accuracy. --fewshot adds labeled EXEMPLARS to the
state and tells Jev to use them for guidance (few-shot via state, as the user asked)."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import Counter
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv("/Users/farhan/work/RAG_Wright/.env")
from rag_wright.ontology.loader import load_actor_synonyms  # noqa: E402

ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
MODEL = "typesafe/jev-1.13"
SPANS = Path("data/compliance/cic1_labels/spans.jsonl")

ROLES = {
    "advertiser": "the advertiser / marketer / manufacturer / brand making the advertising claim",
    "endorser": "the person or entity giving an endorsement, testimonial, review, or influencer post",
    "expert": "an expert whose evaluation or opinion is relied upon",
    "seller": "the seller / vendor / retailer of the goods (when named distinctly from the advertiser)",
    "consumer": "the consumer / customer / audience",
    "employer": "the employer responsible (workplace safety / injury recordkeeping context)",
    "other": "none of the above, multiple parties, or unspecified",
}
EXEMPLARS = [
    ("You must enter each recordable injury on the OSHA 300 Log.", "employer"),
    ("Advertisers must disclose any material connection with an endorser.", "advertiser"),
    ("Endorsements must reflect the honest opinions of the endorser.", "endorser"),
    ("An expert endorser must have conducted an examination at least as rigorous as others in the field.", "expert"),
    ("A seller may not advertise a former price that is not bona fide.", "seller"),
]


def canon(label: str, syn: dict) -> str:
    t = (label or "").strip().lower()
    if not t:
        return "other"
    if "employer" in t or "company had" in t:
        return "employer"
    for r in ("advertiser", "endorser", "expert", "seller", "consumer"):
        if r in t:
            return r
    for s, c in syn.items():  # ttl synonyms: manufacturer->advertiser, influencer->endorser, ...
        if s in t:
            return c
    return "other"


def questions():
    return {"actor": {"type": "choice", "instructions": "Who does this regulatory rule primarily bind or address?",
                      "criteria": ROLES}}


async def jev(client, state, sem):
    async with sem:
        r = await client.post(ENDPOINT, headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"},
                              json={"model": MODEL, "state": state, "questions": questions()}, timeout=60)
        r.raise_for_status()
        a = r.json()["answers"]["actor"]
        return a["choice"], float(a.get("confidence", 0.0)), r.json().get("usage", {}).get("cost", 0.0)


def _state(text, fewshot):
    if not fewshot:
        return text
    ex = "\n".join(f"[{lab}] {t}" for t, lab in EXEMPLARS)
    return f"Reference examples (sentence -> binding party), use them for guidance:\n{ex}\n\nSentence to classify:\n{text}"


async def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--fewshot", action="store_true"); a = ap.parse_args()
    syn = load_actor_synonyms()
    rows = [json.loads(l) for l in SPANS.read_text().splitlines() if l.strip()]
    rules = [r for r in rows if r.get("ok") and r.get("is_rule") and (r.get("actor") or "").strip()]
    gold = [canon(r["actor"], syn) for r in rules]
    n = len(rules)
    print(f"Jev actor test (fewshot={a.fewshot}): {n} rules; gold dist={dict(Counter(gold))}", flush=True)
    sem = asyncio.Semaphore(8)
    async with httpx.AsyncClient() as client:
        res = await asyncio.gather(*(jev(client, _state(r["text"], a.fewshot), sem) for r in rules))
    preds = [c for c, _, _ in res]; cost = sum(c for _, _, c in res)
    acc = sum(p == g for p, g in zip(preds, gold)) / n
    print(f"\nACCURACY (choice == canonical teacher): {acc:.3f}")
    print("per-role recall:")
    for role in ROLES:
        idx = [i for i in range(n) if gold[i] == role]
        if idx:
            print(f"  {role:<11} n={len(idx):<3} recall={sum(preds[i]==role for i in idx)/len(idx):.2f}")
    print(f"\ncost ${cost:.5f} ({n} calls)")


if __name__ == "__main__":
    asyncio.run(main())
