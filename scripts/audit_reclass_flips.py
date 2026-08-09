"""INGEST-LLM-CLASSIFIER (ADR-0048) Phase A audit: are the Gemma real->real flips FIXES or ERRORS?

For each sizeable real->real transition written by the reclassify pass, sample clauses and ask an INDEPENDENT
judge (DeepSeek V4 Pro -- NOT the Gemma classifier, so it can't grade itself) which label better fits the actual
clause text: the OLD (pre-write) label, the NEW (Gemma) label, or NEITHER. Aggregates a per-transition NEW-win
rate and flags transitions to REVERT (where NEW loses). Emits a proposal only -- reverting is a separate step.

  AUDIT_MIN=30 SAMPLE=12 CONC=6 uv run --no-sync python -m scripts.audit_reclass_flips
"""
from __future__ import annotations

import json
import os
from collections import defaultdict

from dotenv import load_dotenv
from pydantic import BaseModel, Field


class Verdict(BaseModel):
    choice: str = Field(json_schema_extra={"enum": ["OLD", "NEW", "NEITHER"]})


def log(m: str) -> None:
    print(m, flush=True)


_PROMPT = (
    "You are auditing a legal clause-function classifier. Below is the text of ONE contract clause, and two "
    "candidate function labels for it. Decide which label better describes the clause's PRIMARY legal function:\n"
    "- OLD: {old}\n- NEW: {new}\n"
    "Answer OLD or NEW. Answer NEITHER only if BOTH are clearly wrong for this clause. Judge the text on its "
    "merits; do not assume the newer label is better.\n\nCLAUSE:\n{text}"
)


def main() -> None:
    os.environ.setdefault("RAG_SERVING", "openrouter")
    os.environ.pop("OPENROUTER_PROVIDER", None)  # DeepSeek 404s under a Cerebras pin
    load_dotenv()
    from rag_wright.models.profiles import DEFAULT_STRUCTURED_REASONING
    from rag_wright.models.seam import build_structured
    from rag_wright.store.arcadedb import ArcadeDBStore
    from rag_wright.util.concurrent import map_concurrent

    audit_min = int(os.environ.get("AUDIT_MIN", "30"))
    sample = int(os.environ.get("SAMPLE", "12"))
    conc = int(os.environ.get("CONC", "6"))
    db = os.environ.get("QA_DB", "ragwright_cuad_full")
    ckpt = "data/eval/taxonomy_gaps/reclass_write_checkpoint.jsonl"

    report = json.load(open("data/eval/taxonomy_gaps/reclass_write_delta_report.json"))
    transitions = {(t["old"], t["new"]): t["count"] for t in report["top_real_transitions"] if t["count"] >= audit_min}
    log(f"[audit] {len(transitions)} transitions with count>={audit_min} to audit (sample {sample} each)")

    # collect up to `sample` (clause_id, span_id) per audited transition from the write checkpoint
    picks: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for line in open(ckpt):
        if not line.strip():
            continue
        for r in json.loads(line)["reclass"]:
            k = (r["old"], r["new"])
            if k in transitions and r["new"] != r["old"] and len(picks[k]) < sample:
                picks[k].append((r["clause_id"], r["span_id"]))

    store = ArcadeDBStore.from_env(database=db)
    all_span_ids = [sid for v in picks.values() for _, sid in v]
    texts = store.span_texts(all_span_ids)
    store.close()

    judge = build_structured(DEFAULT_STRUCTURED_REASONING, Verdict)

    # flat work list: (old, new, span_id)
    work = [(old, new, sid) for (old, new), v in picks.items() for _, sid in v]

    def _judge(item):
        old, new, sid = item
        text = (texts.get(sid) or "").strip()
        if not text:
            return (old, new, None)
        try:
            v = judge.invoke(_PROMPT.format(old=old, new=new, text=text[:1600]))
            return (old, new, v.choice.strip().upper())
        except Exception as e:  # noqa: BLE001
            log(f"  judge err {sid[:30]}: {str(e)[:50]}")
            return (old, new, None)

    log(f"[audit] judging {len(work)} clauses with {DEFAULT_STRUCTURED_REASONING} (conc={conc}) ...")
    results = map_concurrent(work, _judge, max_concurrency=conc, label="[audit]", echo=True, timeout_s=90,
                             timeout_retries=1)

    agg: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"OLD": 0, "NEW": 0, "NEITHER": 0, "n": 0})
    for res in results:
        if res is None:
            continue
        old, new, choice = res
        if choice in ("OLD", "NEW", "NEITHER"):
            agg[(old, new)][choice] += 1
            agg[(old, new)]["n"] += 1

    rows = []
    for (old, new), c in agg.items():
        n = max(1, c["n"])
        new_win = c["NEW"] / n
        verdict = "KEEP" if c["NEW"] > c["OLD"] else "REVERT"  # NEW must beat OLD to stand
        rows.append({"old": old, "new": new, "written": transitions[(old, new)], "judged": c["n"],
                     "new_win_pct": round(100 * new_win), "old_pct": round(100 * c["OLD"] / n),
                     "neither_pct": round(100 * c["NEITHER"] / n), "verdict": verdict})
    rows.sort(key=lambda r: (r["verdict"] == "KEEP", -r["written"]))

    out = "data/eval/taxonomy_gaps/reclass_audit.json"
    revert = [r for r in rows if r["verdict"] == "REVERT"]
    revert_clauses = sum(r["written"] for r in revert)
    json.dump({"audit_min": audit_min, "sample": sample, "rows": rows,
               "revert_transitions": len(revert), "revert_clauses_est": revert_clauses}, open(out, "w"), indent=2)

    log("\n=== FLIP AUDIT (independent DeepSeek judge; NEW must beat OLD to KEEP) ===")
    log(f"{'verdict':8s} {'written':>7s} {'judged':>6s} {'NEW%':>5s} {'OLD%':>5s} {'NEI%':>5s}  transition")
    for r in rows:
        log(f"{r['verdict']:8s} {r['written']:7d} {r['judged']:6d} {r['new_win_pct']:4d}% {r['old_pct']:4d}% "
            f"{r['neither_pct']:4d}%  {r['old']!r} -> {r['new']!r}")
    log(f"\nREVERT {len(revert)} transitions (~{revert_clauses} clauses); KEEP {len(rows) - len(revert)}. "
        f"Proposal saved -> {out} (nothing reverted yet).")


if __name__ == "__main__":
    main()
