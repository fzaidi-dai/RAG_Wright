"""EV-4: the eval runner -- a task over a gold set, concurrently, with progress, repeats and errors kept apart."""
from __future__ import annotations

import asyncio
import functools
import json
import time

import pytest

from rag_wright.api import (EvalReport, GoldItem, GoldSet, arun_eval, rescore_eval, run_eval,
                            score_classification, score_judgment)

GOLD = GoldSet(name="g", version="v1", items=[GoldItem(id=f"i{n}", input=n, expected="even" if n % 2 == 0 else "odd")
                                              for n in range(6)])
CLS = functools.partial(score_classification, floor=0.5)


async def parity(item: GoldItem) -> str:
    await asyncio.sleep(0.01)
    return "even" if item.input % 2 == 0 else "odd"


def test_the_report_carries_outputs_scores_and_what_was_measured():
    report = asyncio.run(arun_eval(GOLD, parity, [CLS], task_name="parity", models=["m-1"]))
    assert isinstance(report, EvalReport) and report.passed is True
    assert (report.gold_set, report.gold_version, report.task, report.models, report.repeats) == (
        "g", "v1", "parity", ["m-1"], 1)
    assert [i.item_id for i in report.items] == [f"i{n}" for n in range(6)]
    assert report.items[3].outputs == ["odd"] and report.items[3].errors == [None]
    assert report.engine_version and report.scores[0].scorer == "classification"
    json.loads(report.model_dump_json())


def test_a_task_that_raises_is_an_error_never_dropped_and_scored_wrong():
    async def flaky(item):
        if item.input == 2:
            raise TimeoutError("182 s")
        return await parity(item)

    report = asyncio.run(arun_eval(GOLD, flaky, [CLS]))
    assert report.errors == 1 and report.items[2].errors == ["TimeoutError: 182 s"] and report.items[2].outputs == [None]
    assert report.scores[0].diagnostics["errors"] == 1
    assert report.scores[0].diagnostics["per_class"]["even"]["recall"] == pytest.approx(2 / 3)


def test_progress_streams_every_job():
    lines: list[str] = []
    asyncio.run(arun_eval(GOLD, parity, [CLS], repeats=2, progress=lines.append))
    assert lines[0] == "[eval] start N=12 (6 items x 2 repeats)"
    assert len([ln for ln in lines if ln.startswith("[eval] ") and "/12 " in ln]) == 12
    assert lines[-1] == "[eval] done passed=True errors=0 flips=0"


def test_repeats_report_flips_and_a_scorer_must_pass_on_every_repeat():
    calls: dict[str, int] = {}

    async def wobbly(item):
        calls[item.id] = calls.get(item.id, 0) + 1
        if item.id in ("i0", "i2", "i4") and calls[item.id] == 2:
            return "odd"  # the second run gets every even item wrong
        return await parity(item)

    report = asyncio.run(arun_eval(GOLD, wobbly, [CLS], repeats=2, concurrency=1))
    assert report.flips == ["i0", "i2", "i4"]
    score = report.scores[0]
    assert score.diagnostics["per_repeat_gate"] == [{"min_class_recall@1": 1.0}, {"min_class_recall@1": 0.0}]
    assert score.gate == {"min_class_recall@1": 0.0} and score.passed is False and report.passed is False


def test_jobs_run_concurrently_up_to_the_limit():
    live = peak = 0

    async def slow(item):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.05)
        live -= 1
        return await parity(item)

    asyncio.run(arun_eval(GOLD, slow, [CLS], concurrency=3))
    assert peak == 3


def test_a_plain_function_runs_off_the_event_loop():
    def blocking(item):
        time.sleep(0.05)
        return "even" if item.input % 2 == 0 else "odd"

    async def run():
        ticks = 0

        async def heartbeat():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        hb = asyncio.create_task(heartbeat())
        report = await arun_eval(GOLD, blocking, [CLS], concurrency=1)
        hb.cancel()
        return report, ticks

    report, ticks = asyncio.run(run())
    assert report.passed is True and ticks >= 15  # 6 x 50 ms of blocking work, the loop kept ticking


def test_rescoring_a_saved_report_calls_no_task():
    report = asyncio.run(arun_eval(GOLD, parity, [CLS]))
    saved = EvalReport.model_validate_json(report.model_dump_json())
    judged = rescore_eval(saved, GOLD, [functools.partial(score_judgment, actionable="odd", precision=1.0,
                                                          recall=1.0)])
    assert judged.items == saved.items and judged.scores[0].scorer == "judgment" and judged.passed is True


def test_the_sync_form_names_the_async_form_inside_a_running_loop():
    assert run_eval(GOLD, parity, [CLS]).passed is True

    async def inside():
        run_eval(GOLD, parity, [CLS])

    with pytest.raises(RuntimeError, match="arun_eval"):
        asyncio.run(inside())
