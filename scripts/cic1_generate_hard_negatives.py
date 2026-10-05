"""CIC-1c hard-negative generation: target the FOUR false-positive patterns from the error analysis (the model
calls these 'rule' but they are NOT binding rules). TRAIN-ONLY silver; seeded by the real FP cases so style
matches. Modal Qwen; preflight + stop-after per the setfit skill."""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv("/Users/farhan/work/RAG_Wright/.env")

from pydantic import BaseModel, Field  # noqa: E402

from rag_wright.models.seam import build_structured  # noqa: E402

MODEL_ID = "qwen3.8-27b-modal"
OUT = Path("data/compliance/cic1_labels/hard_negatives.jsonl")
DOMAINS = ["advertising and endorsements", "workplace safety and recordkeeping", "data privacy", "consumer lending",
           "environmental", "food and drug labeling", "employment", "building and construction"]

# (pattern, instruction, a real FP exemplar from the error analysis)
PATTERNS = [
    ("cross_ref_with_modal",
     "a CROSS-REFERENCE to another provision that happens to contain a modal like 'must'/'shall'/'may' but is NOT itself a rule (it points elsewhere)",
     "(See § 255.3 regarding the product evaluation that an expert endorser must conduct.)"),
    ("hypothetical_example",
     "a HYPOTHETICAL EXAMPLE or illustration describing a scenario (often 'A company ...', 'Suppose ...', 'For example ...') that may quote obligation-like language but is an illustration, NOT a binding rule",
     "A building contractor states in an advertisement disseminated by a paint manufacturer, “I use XYZ exterior paint because it is the most durable.”"),
    ("negated_requirement",
     "a NEGATED requirement stating that NO obligation applies in a situation (e.g. 'No disclosure is required because ...', 'Nothing in this section requires ...')",
     "No disclosure is required because no representation is being made about the product in this context."),
    ("descriptive_rule_verb",
     "a DESCRIPTIVE/factual statement that uses a rule-like verb ('determines', 'reviews', 'provides', 'is responsible') to describe what someone does, but is NOT imposing a binding requirement",
     "The drug company determines the overall subject of the research to test the efficacy of a newly developed drug."),
]


class Batch(BaseModel):
    examples: list[str] = Field(description="Distinct, realistic one-sentence examples, each a standalone sentence as it would appear in a real regulation; each matching the requested pattern.")


def log(m): print(f"[hardneg {time.strftime('%H:%M:%S')}] {m}", flush=True)


def warm():
    import os

    from openai import OpenAI
    b, k = os.environ["VLLM_BASE_URL"], os.environ["VLLM_API_KEY"]
    log(f"[warm] {b} (cold ~480s)..."); t0 = time.time()
    OpenAI(base_url=b, api_key=k, timeout=1000).chat.completions.create(
        model="Qwen/Qwen3.8-27B", messages=[{"role": "user", "content": "Reply one word: ready"}], max_tokens=16, temperature=0)
    log(f"[warm] ready {time.time()-t0:.0f}s")


def prompt(domain, instr, seed, n):
    return (f"Generate {n} distinct, realistic sentences from a {domain} regulation.\n"
            f"Each must be {instr}.\nReal example of this pattern:\n\"{seed}\"\n"
            "Vary wording/specifics; each a single standalone sentence. These are NEGATIVE examples (NOT binding rules). Do not number them.")


async def gen(runnable, domain, pat, instr, seed, n, sem):
    async with sem:
        try:
            out = await runnable.ainvoke(prompt(domain, instr, seed, n))
            return [(e, pat, domain) for e in out.examples]
        except Exception as exc:  # noqa: BLE001
            log(f"  fail [{pat}/{domain}]: {str(exc)[:100]}"); return []


async def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--per", type=int, default=12); ap.add_argument("--concurrency", type=int, default=12); a = ap.parse_args()
    jobs = [(d, p, instr, seed) for d in DOMAINS for (p, instr, seed) in PATTERNS]
    n = len(jobs); log(f"START {n} (pattern x domain) x {a.per}, model={MODEL_ID}")
    runnable = build_structured(MODEL_ID, Batch); warm()
    log("[smoke] 1 batch..."); s = await gen(runnable, jobs[0][0], jobs[0][1], jobs[0][2], jobs[0][3], a.per, asyncio.Semaphore(1))
    if not s: log("[smoke] FAILED"); raise SystemExit(1)
    log(f"[smoke] OK {len(s)} e.g. {s[0][0][:80]!r}")
    sem = asyncio.Semaphore(a.concurrency)
    rows = list(s); tasks = [gen(runnable, d, p, instr, seed, a.per, sem) for (d, p, instr, seed) in jobs[1:]]
    done = 0
    for f in asyncio.as_completed(tasks):
        rows += await f; done += 1
        if done % 5 == 0 or done == len(tasks): log(f"[gen] {done+1}/{n}, {len(rows)} raw")
    seen = set(); kept = []
    for t, pat, dom in rows:
        tt = t.strip()
        if tt.lower() in seen or not (20 <= len(tt) <= 400): continue
        seen.add(tt.lower()); kept.append({"text": tt, "label": "non", "category": f"hardneg_{pat}", "domain": dom, "silver": True})
    OUT.parent.mkdir(parents=True, exist_ok=True); OUT.write_text("".join(json.dumps(r) + "\n" for r in kept))
    from collections import Counter
    log(f"kept={len(kept)} by pattern={dict(Counter(r['category'] for r in kept))}"); log(f"wrote {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
