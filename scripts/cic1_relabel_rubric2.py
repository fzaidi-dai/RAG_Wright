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

from rag_wright.capabilities.requirement_extraction import operative_rule_spans  # noqa: E402
from rag_wright.models.seam import build_structured  # noqa: E402
from rag_wright.subgraphs.compliance_ingestion import is_operative  # noqa: E402

MODEL_ID = "qwen3.8-27b-modal"
OUT = Path("data/compliance/cic1_labels/spans_v3.jsonl")
CORPORA = [("FTC 16 CFR 255", "data/compliance/ftc_16cfr255/16cfr255.sections.json"),
           ("FTC 16 CFR 233", "data/compliance/ftc_16cfr233/ftc_16cfr233.sections.json"),
           ("OSHA 29 CFR 1904", "data/compliance/osha_29cfr1904/osha_29cfr1904.sections.json")]

RUBRIC = """You are a second annotator. Decide: does this sentence, BY ITSELF, STATE A BINDING LEGAL REQUIREMENT that changes a party's legal position?

Answer is_rule = true if it directly commands, forbids, or authorizes conduct, or sets a binding conditional consequence:
  "The lender shall provide a disclosure." (commands) ; "A seller may not misrepresent the price." (forbids) ;
  "An employer may retain the records electronically." (authorizes) ; "A statement is deceptive if it omits a material fact." (binding consequence).

Answer is_rule = false if it does NOT itself bind, which covers:
  - an ILLUSTRATION or worked example, even if it quotes a command ("For example, a store may not advertise a fake sale price."; "A contractor says, 'I use XYZ paint.'");
  - a DEFINITION or a statement of scope/purpose/applicability;
  - a REFERENCE to another provision ("See paragraph (b).");
  - a statement that NO requirement applies ("No notice is required here.");
  - a FACTUAL or explanatory remark, or how something will be judged ("The disclosure will be assessed from the consumer's view."; "The audience may assume the host is sincere.").

Examples:
  "Endorsements must reflect the honest opinions of the endorser." -> is_rule=true (command)
  "For instance, a manufacturer may not affix inflated price tickets." -> is_rule=false (illustration)
  "Some connections may be immaterial because they are insignificant." -> is_rule=false (explanatory)
  "No disclosure is required because no claim is made." -> is_rule=false (no requirement applies)

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
