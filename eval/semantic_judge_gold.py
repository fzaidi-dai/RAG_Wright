"""ING-9 (ADR-0040 Layer 3): the semantic judge on a gold set -- the production Qwen judge vs a Jev judge.

Gold (`data/eval/judge_gold/cases.json`, LOCAL ONLY -- CUAD-derived): the per-dimension classifier test sets for the
SEMANTIC dimensions, run through the deployed classifier fleet; every value the fleet emits for the example's own
dimension is a judge case (supported iff it equals the gold label), plus the gold value if the fleet missed it. So
the refute cases are the classifier's REAL mistakes -- exactly what the judge exists to catch.

  --judge qwen  the production judge: `build_asemantic_judge_fn(model_for(STRUCTURED_REASONING))`, one call per case
  --judge jev   one `jev_decision` call per clause (via the engine invoker) with the SHIPPED request
                (`spans.semantic_judge.judge_request`): the strictness rule once, the clause, one `noul` per value

Writes `data/eval/judge_gold/<judge>.json` (raw verdicts). `--score` prints both side by side against this silver
gold -- which proved unreliable (64% agreement with hand labels); the decision rests on the BLIND HAND-LABELLED
sample (`blind_sample.json` + `blind_labels.json`, scored by `--score-blind`).
Run: `uv run python -u eval/semantic_judge_gold.py --judge jev|qwen` then `--score`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "eval" / "judge_gold"

def log(msg: str) -> None:
    print(msg, flush=True)


async def run(judge: str, concurrency: int) -> None:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    from rag_wright.api import ainvoke_model, load_reference_pack
    from rag_wright.packs.contracts.schemas.property import PropertyDimension
    from rag_wright.models.profiles import ModelRole, model_for
    from rag_wright.packs.contracts.spans.semantic_judge import build_asemantic_judge_fn, judge_request

    load_reference_pack()
    items = json.loads((DATA / "cases.json").read_text())
    sem, done, t0 = asyncio.Semaphore(concurrency), 0, time.time()
    if judge == "qwen":
        ajudge = build_asemantic_judge_fn(model_for(ModelRole.STRUCTURED_REASONING))
        jobs = [(i, j) for i, it in enumerate(items) for j in range(len(it["cases"]))]
    else:
        jobs = [(i, None) for i in range(len(items))]
    log(f"[{judge}] start N={len(jobs)} calls over {len(items)} clauses")

    async def one(i: int, j):
        nonlocal done
        it = items[i]
        dim = PropertyDimension(it["dimension"])
        async with sem:
            try:
                if judge == "qwen":
                    v = await ajudge(dim, it["cases"][j]["value"], it["text"])
                    out = {"item": i, "case": j, "supported": None if v is None else bool(v.supported),
                           "reason": None if v is None else v.reason}
                else:
                    state, questions = judge_request(it["text"], [(dim, c["value"]) for c in it["cases"]])
                    res = await ainvoke_model("jev_decision", {"state": state, "questions": questions}, resources=None)
                    out = {"item": i, "scores": [float((res["answers"].get(f"p{k}") or {}).get("noul", 0.0))
                                                 for k in range(len(it["cases"]))]}
            except Exception as exc:  # noqa: BLE001 - a failed call is recorded, the run continues
                out = {"item": i, "case": j, "error": f"{type(exc).__name__}: {exc}"[:200]}
        done += 1
        if done % 25 == 0 or done == len(jobs):
            log(f"[{judge}] {done}/{len(jobs)} {time.time() - t0:.0f}s")
        return out

    results = await asyncio.gather(*(one(i, j) for i, j in jobs))
    errors = [r for r in results if "error" in r]
    log(f"[{judge}] done {len(results)} calls, {len(errors)} errors" + (f"; first: {errors[0]['error']}" if errors else ""))
    (DATA / f"{judge}.json").write_text(json.dumps(results, indent=1))


def score() -> None:
    items = json.loads((DATA / "cases.json").read_text())
    verdict: dict = {"qwen": {}, "jev": {}, "jev_score": {}}
    if (DATA / "qwen.json").exists():
        for r in json.loads((DATA / "qwen.json").read_text()):
            if "error" not in r:
                verdict["qwen"][(r["item"], r["case"])] = r["supported"]
    if (DATA / "jev.json").exists():
        for r in json.loads((DATA / "jev.json").read_text()):
            if "error" not in r:
                for k, s in enumerate(r["scores"]):
                    verdict["jev"][(r["item"], k)] = s >= 0.5
                    verdict["jev_score"][(r["item"], k)] = s
    gold = {(i, k): c["supported"] for i, it in enumerate(items) for k, c in enumerate(it["cases"])}
    dims = sorted({it["dimension"] for it in items})

    def stats(name: str, keys: list) -> str:
        v = verdict[name]
        have = [k for k in keys if k in v and v[k] is not None]
        if not have:
            return "n/a"
        ok = sum(v[k] == gold[k] for k in have)
        bad = [k for k in have if not gold[k]]
        good = [k for k in have if gold[k]]
        caught = sum(not v[k] for k in bad)
        false_refute = sum(not v[k] for k in good)
        return (f"acc {ok / len(have):.3f} ({ok}/{len(have)}) | catches {caught}/{len(bad)} mistakes | "
                f"refutes {false_refute}/{len(good)} correct values | no-verdict {len(keys) - len(have)}")

    keys = list(gold)
    for name in ("qwen", "jev"):
        log(f"{name:4} ALL  {stats(name, keys)}")
    for d in dims:
        dk = [k for k in keys if items[k[0]]["dimension"] == d]
        log(f"  {d:20} qwen: {stats('qwen', dk)}")
        log(f"  {'':20} jev : {stats('jev', dk)}")
    s = verdict["jev_score"]
    if s:
        log("jev calibration (score bin -> share of gold-supported):")
        for lo in (0.0, 0.2, 0.4, 0.6, 0.8):
            ks = [k for k in s if lo <= s[k] < lo + 0.2 or (lo == 0.8 and s[k] == 1.0)]
            if ks:
                log(f"  [{lo:.1f},{lo + 0.2:.1f}) n={len(ks):3} supported={sum(gold[k] for k in ks) / len(ks):.2f}")
    both = [k for k in keys if k in verdict["qwen"] and verdict["qwen"][k] is not None and k in verdict["jev"]]
    if both:
        log(f"qwen/jev agreement {sum(verdict['qwen'][k] == verdict['jev'][k] for k in both) / len(both):.3f} on {len(both)}")


def score_blind() -> None:
    """Both judges on the hand-labelled sample (labelled blind to their verdicts; `ambiguous` ones excluded)."""
    sample = json.loads((DATA / "blind_sample.json").read_text())
    labels = json.loads((DATA / "blind_labels.json").read_text())
    truth = {n: n in set(labels["supported"]) for n in range(len(sample)) if n not in set(labels["ambiguous"])}
    qwen = {(r["item"], r["case"]): r["supported"] for r in json.loads((DATA / "qwen.json").read_text())}
    jev = {(r["item"], k): s >= 0.5 for r in json.loads((DATA / "jev.json").read_text()) for k, s in enumerate(r["scores"])}
    bad = [n for n, y in truth.items() if not y]
    good = [n for n, y in truth.items() if y]
    for name, v in (("qwen", qwen), ("jev", jev)):
        pred = {n: v[tuple(sample[n])] for n in truth}
        ok = sum(pred[n] == y for n, y in truth.items())
        log(f"{name}: acc {ok}/{len(truth)} = {ok / len(truth):.3f} | catches {sum(not pred[n] for n in bad)}/{len(bad)} "
            f"unsupported | keeps {sum(pred[n] for n in good)}/{len(good)} supported")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--judge", choices=["qwen", "jev"])
    ap.add_argument("--concurrency", type=int, default=12)
    ap.add_argument("--score", action="store_true")
    ap.add_argument("--score-blind", action="store_true")
    a = ap.parse_args()
    if a.score_blind:
        score_blind()
    elif a.score:
        score()
    else:
        asyncio.run(run(a.judge, a.concurrency))
