"""CIC-1c data generation (setfit Phase 4): the teacher (Modal Qwen) GENERATES a large, diverse, balanced TRAIN
set for the operative-rule binary classifier -- the lever I skipped when I treated the 181 real spans as fixed.

Generated examples are TRAIN-ONLY silver (the real in-corpus spans stay the held-out TEST -- an honest transfer
check; caveat: generated text is often stylistically cleaner, so we verify it actually moves the real-test
numbers). Positives = binding rules (obligation/prohibition/permission); negatives = the four non-rule types
(definition, descriptive/epistemic, scope/purpose, cross-reference). Across many regulatory domains, for
generalization. Curated with light reject-rules; marked silver.

Follows the setfit "Run preflight & monitoring": model from the engine profile (Modal Qwen, not a hardcoded id),
.env by explicit path, smoke before fan-out, flushed X/N, stop the app after (the run line bakes in the stop)."""
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
from rag_wright.packs.compliance.ontology.loader import deontic_type_of  # noqa: E402

MODEL_ID = "qwen3.8-27b-modal"
OUT = Path("data/compliance/cic1_labels/generated.jsonl")

DOMAINS = ["advertising and endorsements", "workplace safety and injury recordkeeping", "data privacy and security",
           "financial disclosure and consumer lending", "environmental and emissions", "food and drug labeling",
           "employment and wage-hour", "healthcare and patient records"]

# (category, label, is_positive, instruction)
CATEGORIES = [
    ("obligation", "rule", True, "a BINDING OBLIGATION rule: a regulated party MUST / SHALL / IS REQUIRED TO do something"),
    ("prohibition", "rule", True, "a BINDING PROHIBITION rule: a regulated party MUST NOT / SHALL NOT / MAY NOT do something"),
    ("permission", "rule", True, "a BINDING PERMISSION rule: a regulated party MAY / IS PERMITTED TO do something (a granted allowance)"),
    ("definition", "non", False, "a DEFINITION sentence that defines a term (NOT a rule -- e.g. 'X means ...', 'For purposes of this part, Y is ...')"),
    ("descriptive", "non", False, "a DESCRIPTIVE / EXPLANATORY sentence that states a fact or possibility, NOT a binding rule -- it may even contain 'may' in an epistemic sense (e.g. 'Some connections may be immaterial', 'The ad will likely be interpreted as ...')"),
    ("scope_purpose", "non", False, "a SCOPE or PURPOSE statement describing what a part covers or why it exists (NOT a rule -- e.g. 'This part applies to ...', 'The purpose of this section is ...')"),
    ("cross_reference", "non", False, "a CROSS-REFERENCE sentence pointing to another provision (NOT a rule -- e.g. 'See paragraph (b) of this section', 'as provided in § 1904.7')"),
]


class GeneratedBatch(BaseModel):
    examples: list[str] = Field(description="Distinct, realistic one-sentence examples, each a standalone sentence as it would appear in a real regulation.")


def _prompt(domain: str, instruction: str, n: int) -> str:
    return (f"Generate {n} distinct, realistic example sentences from a {domain} regulation.\n"
            f"Each must be {instruction}.\n"
            "Make them varied in wording and specifics (different actors, conditions, objects); each a single "
            "standalone sentence as it would literally appear in a regulation. Do not number them.")


def log(msg: str) -> None:
    print(f"[cic1-gen {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def warm_endpoint(timeout_s: int = 1000) -> None:
    import os

    from openai import OpenAI
    base, key = os.environ["VLLM_BASE_URL"], os.environ["VLLM_API_KEY"]
    log(f"[warm] warming {base} (cold start ~480s)...")
    t0 = time.time()
    OpenAI(base_url=base, api_key=key, timeout=timeout_s).chat.completions.create(
        model="Qwen/Qwen3.8-27B", messages=[{"role": "user", "content": "Reply with one word: ready"}],
        max_tokens=16, temperature=0)
    log(f"[warm] ready in {time.time()-t0:.0f}s")


def curate(text: str, label: str) -> bool:
    """Light reject-rules (setfit Phase 4). Positives must carry a deontic cue; negatives must NOT read as a plain
    obligation/prohibition. Length + sentence sanity. Dedup handled by the caller."""
    t = (text or "").strip()
    if not (20 <= len(t) <= 400) or t.count(".") > 4:
        return False
    has_cue = deontic_type_of(t) is not None
    if label == "rule":
        return has_cue  # a binding rule must have a deontic cue
    # negatives: reject ones that are actually obligations/prohibitions (must/shall/must not) -- those are rules;
    # a descriptive 'may' (permission cue) is allowed through (that is the hard negative we WANT).
    low = t.lower()
    if any(k in low for k in ("must ", "shall ", "must not", "shall not", "is required", "are required")):
        return False
    return True


async def gen_one(runnable, domain, cat, label, instr, n, sem):
    async with sem:
        try:
            out = await runnable.ainvoke(_prompt(domain, instr, n))
            return [(e, label, cat, domain) for e in out.examples]
        except Exception as exc:  # noqa: BLE001
            log(f"  gen fail [{cat}/{domain}]: {str(exc)[:120]}")
            return []


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per", type=int, default=12, help="examples per (category,domain) call")
    ap.add_argument("--concurrency", type=int, default=12)
    a = ap.parse_args()

    jobs = [(d, c, lab, ispos, instr) for d in DOMAINS for (c, lab, ispos, instr) in CATEGORIES]
    n = len(jobs)
    log(f"START generation: {n} (category x domain) calls x {a.per} examples, model={MODEL_ID}")
    runnable = build_structured(MODEL_ID, GeneratedBatch)
    warm_endpoint()

    # smoke one batch before the fan-out
    log("[smoke] generating 1 batch to verify the path...")
    s = await gen_one(runnable, jobs[0][0], jobs[0][1], jobs[0][2], jobs[0][4], a.per, asyncio.Semaphore(1))
    if not s:
        log("[smoke] FAILED -- no examples returned"); raise SystemExit(1)
    log(f"[smoke] OK -> {len(s)} examples, e.g. {s[0][0][:90]!r}")

    sem = asyncio.Semaphore(a.concurrency)
    tasks = [gen_one(runnable, d, c, lab, instr, a.per, sem) for (d, c, lab, ispos, instr) in jobs[1:]]
    all_rows = list(s)
    done = 0
    for fut in asyncio.as_completed(tasks):
        all_rows += await fut
        done += 1
        if done % 5 == 0 or done == len(tasks):
            log(f"[gen] {done+1}/{n} batches done, {len(all_rows)} raw examples")

    # curate + dedup
    seen = set()
    kept = []
    for text, label, cat, domain in all_rows:
        t = text.strip()
        key = t.lower()
        if key in seen or not curate(t, label):
            continue
        seen.add(key)
        kept.append({"text": t, "label": label, "category": cat, "domain": domain, "silver": True})

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(json.dumps(r) + "\n" for r in kept))
    from collections import Counter
    log("=" * 60)
    log(f"raw={len(all_rows)} kept={len(kept)}  by label={dict(Counter(r['label'] for r in kept))}")
    log(f"by category={dict(Counter(r['category'] for r in kept))}")
    log(f"wrote {OUT}")
    log("REMINDER: stop the Modal app -> uv run --no-sync modal app stop rw-qwen3-modal --yes")


if __name__ == "__main__":
    asyncio.run(main())
