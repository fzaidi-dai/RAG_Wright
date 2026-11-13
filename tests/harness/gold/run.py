"""HD-2, live: today's Qwen tiers and Jev on the D1, D2 and B1 gold sets, three repeats each.

    uv run python -m tests.harness.gold.run [--limit N] [--only NAME ...]

Paid (the pre-flight: about 1,890 calls, under $1). Streams `[eval] i/N` per run; writes each `EvalReport`, Jev's
probabilities and a summary under `data/eval/harness-decisions/<stamp>/` (gitignored, as every eval output is).
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import json
import time
from collections import defaultdict
from pathlib import Path

from rag_wright.api import EvalReport, GoldSet, ScorerResult, arun_eval, engine_capabilities, measure_usage, register_capability
from tests.harness.gold import deciders, gold
from tests.harness.gold.loom import WORKERS
from tests.harness.gold.scoring import score_choice, score_workers

OUT = Path("data/eval/harness-decisions")
REPEATS = 3
#: Not a pass mark yet: HD-3 decides the floor from these numbers. Reported so each run's weakest option is visible.
FLOOR = 0.8


def _runs(kept: defaultdict[str, deciders.Probabilities]) -> list[tuple[str, str, object, object]]:
    """(name, gold set, decider factory, scorer) for every run; factories, so a tier is built only if its run is."""
    choice = functools.partial(score_choice, floor=FLOOR)
    workers = functools.partial(score_workers, workers=tuple(WORKERS), floor=FLOOR)
    runs = []
    for variant in ("small", "wide"):
        runs += [(f"d1-{variant}-qwen", f"d1:{variant}", lambda v=variant: deciders.qwen_d1(v), choice),
                 (f"d1-{variant}-jev-single", f"d1:{variant}",
                  lambda v=variant: deciders.jev_d1(v, kept[f"d1-{v}-jev-single"], split=False), choice),
                 (f"d1-{variant}-jev-split", f"d1:{variant}",
                  lambda v=variant: deciders.jev_d1(v, kept[f"d1-{v}-jev-split"], split=True), choice)]
    runs += [("d2-qwen", "d2", deciders.qwen_d2, workers),
             ("d2-jev", "d2", lambda: deciders.jev_d2(kept["d2-jev"]), workers),
             ("b1-qwen", "b1", deciders.qwen_b1, choice),
             ("b1-jev", "b1", lambda: deciders.jev_b1(kept["b1-jev"]), choice)]
    return runs


def pooled(report: EvalReport, gold_set: GoldSet, scorer) -> ScorerResult:
    """Every repeat scored as one set (each item once per repeat), so a label's accuracy is over all its answers;
    the report's own diagnostics describe the first repeat only. A failed call is a missing answer, counted wrong."""
    items = [item.model_copy(update={"id": f"{item.id}#{r}"}) for item in gold_set.items for r in range(REPEATS)]
    answers = {f"{i.item_id}#{r}": out for i in report.items
               for r, (out, err) in enumerate(zip(i.outputs, i.errors)) if err is None}
    return scorer(gold_set.model_copy(update={"items": items}), answers)


def _gold(spec: str, limit: int | None):
    name, _, variant = spec.partition(":")
    loaded = gold(name, variant=variant or None)
    return loaded if limit is None else loaded.model_copy(update={"items": loaded.items[:limit]})


async def main(limit: int | None, only: list[str]) -> None:
    # the engine ships an empty catalog; a caller registers the engine capabilities it uses
    register_capability(next(m for m in engine_capabilities() if m.slug == "jev_decision"))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = OUT / stamp
    out.mkdir(parents=True, exist_ok=True)
    kept: defaultdict[str, deciders.Probabilities] = defaultdict(deciders.Probabilities)
    runs = [r for r in _runs(kept) if not only or r[0] in only]
    print(f"[hd2] start runs={len(runs)} repeats={REPEATS} out={out}", flush=True)
    summary = {}
    for n, (name, spec, factory, scorer) in enumerate(runs, 1):
        gold_set = _gold(spec, limit)
        print(f"[hd2] {n}/{len(runs)} {name} items={len(gold_set.items)}", flush=True)
        with measure_usage() as usage:
            report = await arun_eval(gold_set, factory(), [scorer], repeats=REPEATS, task_name=name,
                                     models=[deciders.MODEL] if "qwen" in name else ["jev-1.13"],
                                     progress=lambda line, name=name: print(f"[{name}] {line}", flush=True))
        (out / f"{name}.json").write_text(report.model_dump_json(indent=1))
        if name in kept:
            (out / f"{name}.probabilities.json").write_text(json.dumps(kept[name].rows))
        scored = pooled(report, gold_set, scorer)
        summary[name] = {"gate": scored.gate, "accuracy": scored.diagnostics.get("accuracy"),
                         "exact_sets": scored.diagnostics.get("exact_sets"),
                         "per_label": scored.diagnostics.get("per_label"),
                         "errors": sum(e is not None for i in report.items for e in i.errors),
                         "flipped": sum(i.flipped for i in report.items), "calls": usage.calls,
                         "cost_usd": round(usage.cost_usd, 4), "calls_without_cost": usage.calls_without_cost,
                         "ms_per_call": round(usage.latency_ms_total / usage.calls) if usage.calls else None}
        print(f"[hd2] {name} done: {json.dumps({k: v for k, v in summary[name].items() if k != 'per_label'})}",
              flush=True)
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(f"[hd2] done out={out}", flush=True)


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()  # the model keys; a script does not get the test session's setup
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="only the first N items of each set (a smoke run)")
    parser.add_argument("--only", nargs="*", default=[], help="only these runs, by name")
    args = parser.parse_args()
    asyncio.run(main(args.limit, args.only))
