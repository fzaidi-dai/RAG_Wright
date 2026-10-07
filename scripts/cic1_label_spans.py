"""CIC-1b: distillation label generation for the compliance-ingest classifiers.

Teacher = self-hosted Qwen3.8-27B on Modal (profile `qwen3.8-27b-modal`), per the `qwen-vllm-modal` + `setfit`
skills. Follows the setfit "Run preflight & monitoring" discipline:
  * model resolved from the engine profile (NOT a hardcoded served id);
  * `.env` loaded by EXPLICIT path (this script runs from `scripts/`, so a bare load_dotenv() would miss it);
  * ONE-span smoke before the bulk fan-out (catches a dead endpoint / bad schema for the cost of one call);
  * mandatory flushed X/N progress to stdout (run with output to a log you tail / monitor);
  * labels written to JSONL + a data fingerprint; source/section kept per span for source-disjoint splits.

Run (deploy -> label -> ALWAYS stop, one continuous go; see the qwen-vllm-modal cost rule):
  # 1) deploy the server (Config B) -- see the qwen-vllm-modal skill for the exact deploy command
  # 2) uv run --no-sync python scripts/cic1_label_spans.py --out data/compliance/cic1_labels/spans.jsonl ; \
  #    uv run --no-sync modal app stop rw-qwen3-modal --yes     # <- stop baked into the run line
Smoke only (1 span, then exit):
  uv run --no-sync python scripts/cic1_label_spans.py --smoke ; uv run --no-sync modal app stop rw-qwen3-modal --yes
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv("/Users/farhan/work/RAG_Wright/.env")  # preflight #2: explicit path (script runs from scripts/)

from pydantic import BaseModel, Field  # noqa: E402

from rag_wright.packs.compliance.capabilities.requirement_extraction import operative_rule_spans  # noqa: E402
from rag_wright.models.seam import build_structured  # noqa: E402
from rag_wright.packs.compliance.subgraphs.compliance_ingestion import is_operative  # noqa: E402

MODEL_ID = "qwen3.8-27b-modal"  # preflight #1: the engine PROFILE (Modal vLLM backend), not a served id string

CORPORA = [
    ("FTC 16 CFR 255", "data/compliance/ftc_16cfr255/16cfr255.sections.json"),
    ("FTC 16 CFR 233", "data/compliance/ftc_16cfr233/ftc_16cfr233.sections.json"),
    ("OSHA 29 CFR 1904", "data/compliance/osha_29cfr1904/osha_29cfr1904.sections.json"),
]


class SpanLabel(BaseModel):
    """The teacher's judgment of ONE candidate span: is it a binding rule, and (if so) its field tags."""

    is_rule: bool = Field(
        description=("True IFF this sentence is a BINDING operative rule -- an obligation, prohibition, or "
                     "permission that imposes or grants a requirement. FALSE for a definition, a descriptive / "
                     "explanatory statement (e.g. 'connections may be immaterial' uses 'may' in a NON-binding, "
                     "epistemic sense), a scope / purpose statement, or a cross-reference."))
    rationale: str = Field(default="", description="One short clause: why is_rule is true or false.")
    actor: str = Field(default="", description="Who the rule binds (advertiser, endorser, employer, ...). Empty if not a rule.")
    claim_types: list[str] = Field(
        default_factory=list,
        description=("Advertising claim types the rule applies to, from: efficacy, comparative, pricing, health, "
                     "environmental, endorsement, performance, guarantee. Empty if non-advertising or not a rule."))
    applicability: list[str] = Field(
        default_factory=list,
        description="Conditions 'dimension: value' under which the rule applies (e.g. 'jurisdiction: California'). Empty if unconditional / not a rule.")
    evidence_standard: str = Field(default="", description="Substantiation the rule requires, if any. Empty otherwise.")


_PROMPT = (
    "You are labeling sentences from a regulation to build a training set for a classifier.\n\n"
    "Sentence (from {source} §{section}):\n\"\"\"\n{span}\n\"\"\"\n\n"
    "1) is_rule: is this a BINDING operative rule (an obligation / prohibition / permission that imposes or grants "
    "a requirement), or NOT a rule (a definition, a descriptive or explanatory statement, a scope / purpose line, "
    "or a cross-reference)? A modal like 'may' can appear in a NON-binding, descriptive sense "
    "('connections may be immaterial') — that is NOT a rule.\n"
    "2) If it IS a rule, fill actor / claim_types / applicability / evidence_standard; otherwise leave them empty.\n"
)


def log(msg: str) -> None:
    print(f"[cic1-label {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def warm_endpoint(timeout_s: int = 1000) -> None:
    """Preflight: warm the server with a PLAIN completion (long timeout) BEFORE any structured call. The structured
    path's per-call timeout (RAG_STRUCTURED_TIMEOUT_S, ~60-150s x bounded retries) is far shorter than the ~480s
    cold start, so a structured smoke issued against a cold-starting container times out and gives up before the
    server is up (observed 2026-10-04: 3x60s = 180s < 480s boot). Warm first, then structured calls hit a ready
    server. qwen-vllm-modal cold-start gotcha."""
    import os

    from openai import OpenAI

    base, key = os.environ["VLLM_BASE_URL"], os.environ["VLLM_API_KEY"]
    log(f"[warm] warming {base} with a plain completion (cold start ~480s)...")
    t0 = time.time()
    OpenAI(base_url=base, api_key=key, timeout=timeout_s).chat.completions.create(
        model="Qwen/Qwen3.8-27B",
        messages=[{"role": "user", "content": "Reply with exactly one word: ready"}],
        max_tokens=16, temperature=0)
    log(f"[warm] server ready in {time.time()-t0:.0f}s")


def collect_spans() -> list[dict]:
    """Candidate spans = operative_rule_spans over every OPERATIVE section of each corpus, tagged with
    (source, section, span_index) so the training split can be source/section-disjoint (setfit Phase 1)."""
    recs: list[dict] = []
    for source, path in CORPORA:
        p = Path(path)
        if not p.exists():
            log(f"WARN: corpus missing, skipping: {path}")
            continue
        for sec in json.loads(p.read_text()):
            txt = (sec.get("text") or "").strip()
            if not txt or not is_operative(txt):
                continue
            for i, (span, deontic) in enumerate(operative_rule_spans(txt)):
                recs.append({"source": source, "section": sec["section"], "span_index": i,
                             "text": span, "deontic": deontic})
    return recs


async def label_one(runnable, rec: dict, sem: asyncio.Semaphore) -> dict:
    async with sem:
        try:
            out = await runnable.ainvoke(_PROMPT.format(source=rec["source"], section=rec["section"], span=rec["text"]))
            return {**rec, "ok": True, **out.model_dump()}
        except Exception as exc:  # noqa: BLE001 - never crash the run on one span; record + move on
            return {**rec, "ok": False, "error": str(exc)[:200]}


def _fingerprint(recs: list[dict]) -> str:
    h = hashlib.sha256()
    h.update(MODEL_ID.encode())
    for r in recs:
        h.update(f"{r['source']}|{r['section']}|{r['span_index']}|{r['text']}".encode())
    return h.hexdigest()[:16]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/compliance/cic1_labels/spans.jsonl")
    ap.add_argument("--limit", type=int, default=0, help="cap spans (0 = all)")
    ap.add_argument("--concurrency", type=int, default=12, help="<=16 (Modal client cap)")
    ap.add_argument("--smoke", action="store_true", help="label ONE span, then exit (preflight)")
    a = ap.parse_args()

    recs = collect_spans()
    if a.limit:
        recs = recs[: a.limit]
    n = len(recs)
    if not n:
        log("no candidate spans found -- aborting")
        return
    fp = _fingerprint(recs)
    log(f"START label-gen: {n} spans, model={MODEL_ID}, concurrency={a.concurrency}, fingerprint={fp}")

    runnable = build_structured(MODEL_ID, SpanLabel)

    # preflight: warm the server (plain completion) so the structured smoke/fan-out doesn't race the cold start.
    warm_endpoint()

    # preflight #3: smoke ONE span end-to-end (now against a WARM server) before the fan-out.
    log("[smoke] labeling 1 span to verify the structured path...")
    t0 = time.time()
    first = await label_one(runnable, recs[0], asyncio.Semaphore(1))
    if not first.get("ok"):
        log(f"[smoke] FAILED in {time.time()-t0:.0f}s: {first.get('error')}")
        raise SystemExit(1)
    log(f"[smoke] OK in {time.time()-t0:.0f}s -> is_rule={first['is_rule']} actor={first.get('actor')!r} "
        f"rationale={first.get('rationale','')[:80]!r}")

    out_path = Path(a.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if a.smoke:
        out_path.with_suffix(".smoke.json").write_text(json.dumps(first, indent=2))
        log(f"[smoke] wrote {out_path.with_suffix('.smoke.json')} -- smoke only, exiting")
        return

    # bulk fan-out (preflight #4: flushed X/N progress). order doesn't matter (each rec carries its coords).
    sem = asyncio.Semaphore(a.concurrency)
    results = [first]
    rest = [label_one(runnable, r, sem) for r in recs[1:]]
    done = 0
    for fut in asyncio.as_completed(rest):
        results.append(await fut)
        done += 1
        if done % 5 == 0 or done == len(rest):
            ok = sum(1 for r in results if r.get("ok"))
            log(f"[label] {done+1}/{n} done ({ok} ok, {len(results)-ok} err)")

    with out_path.open("w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
    ok = sum(1 for r in results if r.get("ok"))
    rules = sum(1 for r in results if r.get("ok") and r.get("is_rule"))
    log("=" * 70)
    log(f"DONE: {n} spans -> {ok} labeled ok, {n-ok} errors; is_rule=true for {rules}/{ok}")
    log(f"wrote {out_path} (fingerprint {fp})")
    log("REMINDER: stop the Modal app now -> uv run --no-sync modal app stop rw-qwen3-modal --yes")


if __name__ == "__main__":
    asyncio.run(main())
