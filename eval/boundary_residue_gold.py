"""ING-4d (ADR-0122/0124): LIVE accuracy + repeatability of the provision-boundary residue prompt against a
hand-labelled gold set -- the SHIPPED request (`spans.boundary.residue_request`) sent through the `jev_decision`
capability (the engine invoker), `--repeats` times per document.

Gold + residue lines live in `data/eval/jev_variance/` (LOCAL ONLY: CUAD text is restrictively licensed and the
non-contract document is client data -- never committed):
  gold.json + compact_samples.json        tuning pair (2 contracts)
  heldout_gold.json + heldout_items.json  held-out (7 contracts + 1 lab report), labelled before any prompt ran
Each gold entry lists the `start` line indices; `ambiguous` ones are not scored.

Reports per document: accuracy (scores averaged over the repeats), per-call accuracy, lines whose answer flips
between calls, and the smallest distance of a scored line from the 0.5 threshold.

Run: `uv run python -u eval/boundary_residue_gold.py --repeats 3`. Writes `data/eval/jev_variance/residue_gold_report.json`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics as st
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "eval" / "jev_variance"


def log(msg: str) -> None:
    print(msg, flush=True)


def load_sets() -> dict:
    """{doc_key: (lines, gold)} for the tuning pair and the held-out set."""
    tuning, tgold = json.loads((DATA / "compact_samples.json").read_text())["items"], json.loads((DATA / "gold.json").read_text())
    held, hgold = json.loads((DATA / "heldout_items.json").read_text()), json.loads((DATA / "heldout_gold.json").read_text())
    sets = {k: ([line for _, line in v], tgold[k]) for k, v in tuning.items()}
    sets.update({k: ([line for _, line in v["items"]], hgold[k]) for k, v in held.items()})
    return sets


async def main(repeats: int, concurrency: int, only: list[str] | None = None) -> None:
    from dotenv import load_dotenv

    from rag_wright.api import ainvoke_model, load_reference_pack
    from rag_wright.packs.contracts.spans.boundary import _THRESHOLD, residue_request

    load_dotenv(ROOT / ".env")
    load_reference_pack()
    sets = load_sets()
    if only:
        sets = {k: v for k, v in sets.items() if k in only}
    jobs = [k for k in sets for _ in range(repeats)]
    sem, done = asyncio.Semaphore(concurrency), 0
    log(f"[gold] start N={len(jobs)} calls ({len(sets)} documents x {repeats})")

    async def run(key: str) -> tuple[str, list[float]]:
        nonlocal done
        state, questions = residue_request(sets[key][0])
        async with sem:
            out = await ainvoke_model("jev_decision", {"state": state, "questions": questions}, resources=None)
        done += 1
        log(f"[gold] {done}/{len(jobs)} {key}")
        return key, [float((out["answers"].get(f"c{i}") or {}).get("noul", 0.0)) for i in range(len(sets[key][0]))]

    results = await asyncio.gather(*(run(k) for k in jobs))
    report, right, total = {}, 0, 0
    for key, (lines, gold) in sets.items():
        runs = [s for k, s in results if k == key]
        truth = {i: i in gold["start"] for i in range(len(lines)) if i not in gold["ambiguous"]}
        mean = [st.mean(r[i] for r in runs) for i in range(len(lines))]
        wrong = [i for i, y in truth.items() if (mean[i] >= _THRESHOLD) != y]
        report[key] = {
            "scored": len(truth), "correct": len(truth) - len(wrong),
            "per_call": [sum((r[i] >= _THRESHOLD) == y for i, y in truth.items()) / len(truth) for r in runs],
            "flips": sum(len({r[i] >= _THRESHOLD for r in runs}) > 1 for i in truth),
            "min_margin": round(min(abs(mean[i] - _THRESHOLD) for i in truth), 3) if truth else None,
            "wrong": [{"line": lines[i][:100], "gold": truth[i], "score": round(mean[i], 3)} for i in wrong],
            "near": [{"line": lines[i][:100], "gold": truth[i], "scores": [round(r[i], 3) for r in runs]}
                     for i in truth if i not in wrong and abs(mean[i] - _THRESHOLD) < 0.15],
            "scores": runs}
        right, total = right + len(truth) - len(wrong), total + len(truth)
        r = report[key]
        log(f"[gold] {key:9} {r['correct']}/{r['scored']} per-call={[round(a, 3) for a in r['per_call']]} "
            f"flips={r['flips']} min_margin={r['min_margin']}")
        for w in r["wrong"]:
            log(f"[gold]     WRONG gold={'start' if w['gold'] else 'no'} score={w['score']} {w['line']!r}")
        for w in r["near"]:
            log(f"[gold]     near  gold={'start' if w['gold'] else 'no'} scores={w['scores']} {w['line']!r}")
    log(f"[gold] TOTAL {right}/{total} = {right / total:.3f}")
    (DATA / f"residue_gold_report{'_' + '_'.join(only) if only else ''}.json").write_text(json.dumps({"total": [right, total], "documents": report}, indent=2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--only", nargs="*", help="document keys to run (default: all)")
    a = ap.parse_args()
    asyncio.run(main(a.repeats, a.concurrency, a.only))
