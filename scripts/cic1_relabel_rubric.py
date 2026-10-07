"""CIC-1c: RELABEL the real spans under the crisp operative-rule rubric (the user-agreed task definition), so the
gold (train + test) is COHERENT -- the 0.85 ceiling was label inconsistency on example-embedded modals, not data.
Teacher = Modal Qwen (a labeling pass; the final classifier is local). Outputs spans_v2.jsonl {..., is_rule, reason}."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv("/Users/farhan/work/RAG_Wright/.env")

from pydantic import BaseModel, Field  # noqa: E402

from rag_wright.packs.compliance.capabilities.requirement_extraction import operative_rule_spans  # noqa: E402
from rag_wright.models.seam import build_structured  # noqa: E402
from rag_wright.packs.compliance.subgraphs.compliance_ingestion import is_operative  # noqa: E402

MODEL_ID = "qwen3.8-27b-modal"
OUT = Path("data/compliance/cic1_labels/spans_v2.jsonl")
CORPORA = [("FTC 16 CFR 255", "data/compliance/ftc_16cfr255/16cfr255.sections.json"),
           ("FTC 16 CFR 233", "data/compliance/ftc_16cfr233/ftc_16cfr233.sections.json"),
           ("OSHA 29 CFR 1904", "data/compliance/osha_29cfr1904/osha_29cfr1904.sections.json")]

RUBRIC = """Classify whether this regulatory sentence is ITSELF a BINDING OPERATIVE RULE.

Label is_rule = true ONLY if the sentence, on its own, imposes or grants a binding requirement:
- an obligation (must / shall / required / responsible for), a prohibition (must not / shall not / may not /
  prohibited), a permission or allowance (may / permitted / entitled), OR a binding conditional consequence
  (e.g. "A practice is a deceptive practice if ...", "X is a violation when ...").

Label is_rule = false for EVERYTHING ELSE, which explicitly INCLUDES:
- an illustrative EXAMPLE or hypothetical, EVEN IF it contains modal/obligation words ("For instance, a
  manufacturer may not ...", "A company states, '...'", "An advertiser who claims X is misleading ...") -- an
  example OF a rule is not itself the operative rule;
- a definition ("X means ...", "For purposes of this part ..."), a scope/purpose statement ("This part applies
  ...", "The purpose is ..."), a cross-reference ("See § 255.3", "as provided in ...");
- a NEGATED requirement ("No disclosure is required ...", "Neither are sellers required ...", "Nothing in this
  section requires ...");
- a DESCRIPTIVE statement of fact, consequence, or how something will be evaluated ("The adequacy will be
  evaluated ...", "The company determines ...", "connections may be immaterial ...").

Sentence:
\"\"\"{span}\"\"\"
"""


class OperativeLabel(BaseModel):
    is_rule: bool = Field(description="True iff the sentence is itself a binding operative rule per the rubric.")
    reason: str = Field(default="", description="One short clause: which rubric category it falls under.")


def log(m): print(f"[relabel {time.strftime('%H:%M:%S')}] {m}", flush=True)


def warm():
    import os

    from openai import OpenAI
    b, k = os.environ["VLLM_BASE_URL"], os.environ["VLLM_API_KEY"]
    log(f"[warm] {b} (cold ~480s)..."); t0 = time.time()
    OpenAI(base_url=b, api_key=k, timeout=1000).chat.completions.create(
        model="Qwen/Qwen3.8-27B", messages=[{"role": "user", "content": "Reply one word: ready"}], max_tokens=16, temperature=0)
    log(f"[warm] ready {time.time()-t0:.0f}s")


async def one(runnable, rec, sem):
    async with sem:
        try:
            out = await runnable.ainvoke(RUBRIC.format(span=rec["text"]))
            return {**rec, "ok": True, "is_rule": out.is_rule, "reason": out.reason}
        except Exception as exc:  # noqa: BLE001
            return {**rec, "ok": False, "error": str(exc)[:160]}


async def main():
    recs = []
    for source, path in CORPORA:
        for sec in json.loads(Path(path).read_text()):
            txt = (sec.get("text") or "").strip()
            if not txt or not is_operative(txt):
                continue
            for i, (span, deontic) in enumerate(operative_rule_spans(txt)):
                recs.append({"source": source, "section": sec["section"], "span_index": i, "text": span, "deontic": deontic})
    n = len(recs); log(f"START relabel {n} spans with the crisp rubric, model={MODEL_ID}")
    runnable = build_structured(MODEL_ID, OperativeLabel); warm()
    log("[smoke] 1 span..."); s = await one(runnable, recs[0], asyncio.Semaphore(1))
    if not s.get("ok"): log(f"[smoke] FAILED {s.get('error')}"); raise SystemExit(1)
    log(f"[smoke] OK is_rule={s['is_rule']} :: {s.get('reason','')[:60]}")
    sem = asyncio.Semaphore(12)
    results = [s]; tasks = [one(runnable, r, sem) for r in recs[1:]]; done = 0
    for f in asyncio.as_completed(tasks):
        results.append(await f); done += 1
        if done % 10 == 0 or done == len(tasks):
            okc = sum(1 for r in results if r.get("ok")); log(f"[relabel] {done+1}/{n} ({okc} ok)")
    OUT.write_text("".join(json.dumps(r) + "\n" for r in results))
    from collections import Counter
    rules = sum(1 for r in results if r.get("ok") and r["is_rule"])
    log(f"DONE: {sum(1 for r in results if r.get('ok'))} ok, is_rule=true for {rules}")
    log(f"by deontic of rules: {dict(Counter(r['deontic'] for r in results if r.get('ok') and r['is_rule']))}")
    log(f"wrote {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
