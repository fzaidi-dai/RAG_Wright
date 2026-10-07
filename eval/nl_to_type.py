"""CU-D2: NL->type eval. The other half of the front door (CU-D1 assumed the type was given; this measures
whether `understand_query` recovers the RIGHT type from a natural-language question).

Eval set is LLM-GENERATED + spot-checked (per the standing decision -- no hand gold yet): for each taxonomy
label, a generator model writes varied colloquial user questions WITHOUT parroting the label; plus hand-picked
OUT-OF-TAXONOMY probes (concepts genuinely absent from the taxonomy) and constructed MULTI-TYPE questions. The
generator is GENERAL (Gemma) while `understand_query` uses STRUCTURED_REASONING (DeepSeek Pro), so this is a
CROSS-MODEL check, not a model agreeing with itself. The generated set is cached (stable spot-check); the
predictions re-run each time. All LLM calls are concurrent (the standing eval-concurrency rule).

Metrics:
- In-taxonomy type accuracy: gold label in predicted clause_types (any-match) + exact-match.
- Multi-type: both gold labels in predicted clause_types.
- Out-of-taxonomy detection: in_taxonomy == False on the probes.

Records a durable report (aggregate + a spot-check sample) at docs/eval/nl_to_type_cu-d2.md.

Usage:  uv run --no-sync python -m eval.nl_to_type          # N=4 per label
        REGEN=1 uv run --no-sync python -m eval.nl_to_type  # force-regenerate the eval set
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

from rag_wright.packs.contracts.capabilities.query_understanding import understand_query
from rag_wright.packs.contracts.schemas.function import FUNCTION_LABELS
from rag_wright.models.profiles import ModelRole, model_for
from rag_wright.models.seam import build_structured

N = int(os.environ.get("N", "4"))
CONC = int(os.environ.get("CONC", "8"))
PRED = os.environ.get("PRED_MODEL", "gemma")  # "gemma" (GENERAL, the production default) | "pro" -- A/B knob
SET_CACHE = Path("data/cache/cuad/nl_eval_set.json")
REPORT = Path(f"docs/eval/nl_to_type_cu-d2{'' if PRED == 'gemma' else '_' + PRED}.md")

OUT_OF_TAX = ["Force Majeure", "Confidentiality", "Arbitration", "Data Privacy",
              "Payment Terms", "Severability", "Entire Agreement", "Counterparts"]
MULTI_PAIRS = [("Governing Law", "Insurance"), ("Non-Compete", "Exclusivity"),
               ("Governing Law", "Indemnification"), ("Cap On Liability", "Insurance"),
               ("Parties", "Effective Date"), ("Audit Rights", "Post-Termination Services"),
               ("License Grant", "IP Ownership Assignment"), ("Termination For Convenience", "Renewal Term")]

_INTAX = ("A contract has a '{concept}' clause. Write {n} natural, varied questions a real end user (not a "
          "lawyer) might type to find or ask about that clause in a specific contract. Colloquial and diverse; "
          "do NOT use the exact phrase '{concept}'.")
_OUTTAX = ("A contract has a '{concept}' clause. Write {n} natural questions a real user might type to find it "
           "in a specific contract. Colloquial; do NOT use the exact phrase '{concept}'.")
_MULTI = ("Write ONE natural question a user might type that asks about BOTH the '{a}' clause AND the '{b}' "
          "clause of a specific contract, in a single sentence.")


class _Queries(BaseModel):
    queries: list[str] = []


def type_hit(pred: list[str], gold: list[str]) -> bool:
    """In-taxonomy any-match: the (single) gold label is among the predicted types."""
    return bool(gold) and gold[0] in pred


def exact_hit(pred: list[str], gold: list[str]) -> bool:
    return pred == gold


def multi_hit(pred: list[str], gold: list[str]) -> bool:
    """Multi-type: every gold label is recovered (order-insensitive)."""
    return set(gold) <= set(pred)


def _progress(m: str) -> None:
    print(m, flush=True)


def _gen(instruction: str) -> list[str]:
    out = build_structured(model_for(ModelRole.GENERAL), _Queries).invoke(instruction)
    return [q.strip() for q in out.queries if q.strip()]


async def _build_set() -> list[dict]:
    sem = asyncio.Semaphore(CONC)

    async def intax(label):
        async with sem:
            qs = await asyncio.to_thread(_gen, _INTAX.format(concept=label, n=N))
        return [{"query": q, "gold": [label], "kind": "intax"} for q in qs[:N]]

    async def outtax(concept):
        async with sem:
            qs = await asyncio.to_thread(_gen, _OUTTAX.format(concept=concept, n=3))
        return [{"query": q, "gold": [], "kind": "outtax", "concept": concept} for q in qs[:3]]

    async def multi(pair):
        async with sem:
            qs = await asyncio.to_thread(_gen, _MULTI.format(a=pair[0], b=pair[1]))
        return [{"query": q, "gold": list(pair), "kind": "multi"} for q in qs[:1]]

    groups = await asyncio.gather(
        *[intax(l) for l in FUNCTION_LABELS], *[outtax(c) for c in OUT_OF_TAX], *[multi(p) for p in MULTI_PAIRS])
    return [item for g in groups for item in g]


async def _predict(eval_set: list[dict]) -> list[dict]:
    sem = asyncio.Semaphore(CONC)
    done = 0
    pred_model = model_for(ModelRole.STRUCTURED_REASONING if PRED == "pro" else ModelRole.GENERAL)

    async def one(item):
        nonlocal done
        async with sem:
            intent = await asyncio.to_thread(understand_query, item["query"], model_id=pred_model)
        done += 1
        if done % 40 == 0 or done == len(eval_set):
            _progress(f"[predict] {done}/{len(eval_set)}")
        return {**item, "pred": intent.clause_types, "in_taxonomy": intent.in_taxonomy}

    return await asyncio.gather(*[one(i) for i in eval_set])


def main() -> None:
    load_dotenv()
    if SET_CACHE.exists() and not os.environ.get("REGEN"):
        eval_set = json.loads(SET_CACHE.read_text())
        _progress(f"[set] loaded {len(eval_set)} queries from cache")
    else:
        t = time.perf_counter()
        eval_set = asyncio.run(_build_set())
        SET_CACHE.parent.mkdir(parents=True, exist_ok=True)
        SET_CACHE.write_text(json.dumps(eval_set, indent=2))
        _progress(f"[set] generated {len(eval_set)} queries in {time.perf_counter() - t:.1f}s (cached)")

    t = time.perf_counter()
    preds = asyncio.run(_predict(eval_set))
    _progress(f"[predict] {len(preds)} in {time.perf_counter() - t:.1f}s")

    intax = [p for p in preds if p["kind"] == "intax"]
    outtax = [p for p in preds if p["kind"] == "outtax"]
    multi = [p for p in preds if p["kind"] == "multi"]
    any_hit = sum(type_hit(p["pred"], p["gold"]) for p in intax)
    exact = sum(exact_hit(p["pred"], p["gold"]) for p in intax)
    oot_detected = sum(not p["in_taxonomy"] for p in outtax)
    multi_both = sum(multi_hit(p["pred"], p["gold"]) for p in multi)
    frac = lambda a, b: a / b if b else 0.0

    print("\n=== NL->type eval (CU-D2) ===", flush=True)
    print(f"  in-taxonomy queries={len(intax)}  out-of-tax={len(outtax)}  multi-type={len(multi)}", flush=True)
    print(f"  TYPE ACCURACY (any-match) {any_hit}/{len(intax)} = {frac(any_hit, len(intax)):.3f}", flush=True)
    print(f"  TYPE ACCURACY (exact)     {exact}/{len(intax)} = {frac(exact, len(intax)):.3f}", flush=True)
    print(f"  MULTI-TYPE (both)         {multi_both}/{len(multi)} = {frac(multi_both, len(multi)):.3f}", flush=True)
    print(f"  OUT-OF-TAX detection      {oot_detected}/{len(outtax)} = {frac(oot_detected, len(outtax)):.3f}",
          flush=True)

    _write_report(intax, outtax, multi, any_hit, exact, oot_detected, multi_both, frac)
    _progress(f"[report] wrote {REPORT}")


def _write_report(intax, outtax, multi, any_hit, exact, oot_detected, multi_both, frac) -> None:
    misses = [p for p in intax if p["gold"][0] not in p["pred"]]
    lines = [
        "# CU-D2: NL->type eval results",
        "",
        f"> Regenerated by `uv run --no-sync python -m eval.nl_to_type` (PRED_MODEL={PRED}). Eval set is "
        f"LLM-generated (Gemma) + spot-checked; predictions by `understand_query` (two-step reason->emit, "
        f"model={'Gemma/GENERAL' if PRED != 'pro' else 'DeepSeek Pro'}). See ADR-0032 for the Gemma-vs-Pro A/B.",
        "",
        "## Metrics",
        "",
        "| metric | value |",
        "|---|---|",
        f"| Type accuracy (any-match) | **{frac(any_hit, len(intax)):.3f}** ({any_hit}/{len(intax)}) |",
        f"| Type accuracy (exact set) | {frac(exact, len(intax)):.3f} ({exact}/{len(intax)}) |",
        f"| Multi-type (both recovered) | {frac(multi_both, len(multi)):.3f} ({multi_both}/{len(multi)}) |",
        f"| Out-of-taxonomy detection | {frac(oot_detected, len(outtax)):.3f} ({oot_detected}/{len(outtax)}) |",
        "",
        "## Spot-check sample",
        "",
        "### In-taxonomy (query -> gold => predicted)",
        "",
    ]
    for p in intax[:12]:
        mark = "OK" if p["gold"][0] in p["pred"] else "MISS"
        lines.append(f"- [{mark}] {p['query']!r} -> {p['gold'][0]!r} => {p['pred']}")
    lines += ["", "### Multi-type", ""]
    for p in multi:
        mark = "OK" if set(p["gold"]) <= set(p["pred"]) else "PARTIAL"
        lines.append(f"- [{mark}] {p['query']!r} -> {p['gold']} => {p['pred']}")
    lines += ["", "### Out-of-taxonomy probes (should be detected: pred=[], in_taxonomy=False)", ""]
    for p in outtax:
        mark = "OK" if not p["in_taxonomy"] else "LEAK"
        lines.append(f"- [{mark}] ({p.get('concept','')}) {p['query']!r} => in_taxonomy={p['in_taxonomy']} pred={p['pred']}")
    lines += ["", f"## In-taxonomy misses ({len(misses)})", ""]
    for p in misses:
        lines.append(f"- {p['query']!r} -> gold {p['gold'][0]!r} => predicted {p['pred']}")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
