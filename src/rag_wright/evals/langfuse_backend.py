"""EV-5: the optional Langfuse backend of `arun_eval`.

The gold set becomes a dataset (`<gold name>`, items `<gold name>:<item id>`, items no longer in the gold set
archived) whose version is the latest server timestamp the sync wrote; each repeat is a `run_experiment` on that
version, run off the event loop (EV-0 finding 4: it blocks the caller's loop). The task wrapper never raises (EV-0
finding 2: a raising task is dropped): a failed call returns an error marker, which an `error` item score flags and
the report records as an error in its slot. Each scorer's gates and pass/fail are pushed as run scores; the report is
scored locally, from the same outputs, exactly as the local runner scores it. Every model call made inside a task
nests under that item's trace (the SDK's item span is the current context). Nothing reads observations, so no
endpoint the self-hosted server lacks is used (EV-4: the v2 observations list answers 404).
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Optional, Sequence

from rag_wright.contracts.evals import EvalReport, GoldSet, ItemResult


def _dataset_items(client: Any, gold: GoldSet) -> str:
    """Upsert the gold set as a dataset and archive what it no longer holds; returns the version (ISO timestamp)."""
    client.create_dataset(name=gold.name, description=gold.description or None,
                          metadata={"gold_version": gold.version, "source": "rag_wright.evals"})
    existing = list(client.get_dataset(gold.name).items)
    keep = {f"{gold.name}:{i.id}" for i in gold.items}
    stamps = []
    for item in gold.items:
        meta = {**item.metadata, "gold_id": item.id, "group": item.group, "provenance": item.provenance,
                "gold_version": gold.version}
        stamps.append(client.create_dataset_item(dataset_name=gold.name, id=f"{gold.name}:{item.id}",
                                                 input=item.input, expected_output=item.expected,
                                                 metadata=meta).updated_at)
    for old in existing:
        if old.id not in keep:
            stamps.append(client.create_dataset_item(dataset_name=gold.name, id=old.id, input=old.input,
                                                     expected_output=old.expected_output, metadata=old.metadata,
                                                     status="ARCHIVED").updated_at)
    return max(stamps).isoformat()


async def arun_on_langfuse(gold: GoldSet, task: Callable, scorers: Sequence[Callable], *, repeats: int,
                           concurrency: int, say: Callable[[str], Any], task_name: str, models: Sequence[str],
                           run_name: Optional[str], client: Any, call: Callable, score: Callable,
                           engine_version: str) -> EvalReport:
    from datetime import datetime

    from langfuse import Evaluation

    version = await asyncio.to_thread(_dataset_items, client, gold)
    dataset = await asyncio.to_thread(client.get_dataset, gold.name, version=datetime.fromisoformat(version))
    by_id = {i.id: i for i in gold.items}
    n = len(gold.items) * repeats
    outputs = {i.id: [None] * repeats for i in gold.items}
    errors: dict[str, list[Optional[str]]] = {i.id: [None] * repeats for i in gold.items}
    urls: list[str] = []
    done = 0
    base = run_name or f"{task_name} {time.strftime('%Y-%m-%d %H:%M:%S')}"
    say(f"[eval] start N={n} ({len(gold.items)} items x {repeats} repeats)")

    def make_task(r: int) -> Callable:
        async def lf_task(*, item: Any, **_: Any) -> dict:
            nonlocal done
            gid = item.metadata["gold_id"]
            try:
                out: dict = {"output": await call(task, by_id[gid])}
            except Exception as exc:  # noqa: BLE001 - never raise: run_experiment would drop the item
                out = {"error": f"{type(exc).__name__}: {exc}"}
            done += 1
            say(f"[eval] {done}/{n} {gid} r{r + 1} " + (f"error: {out['error']}" if "error" in out else "ok"))
            return out
        return lf_task

    def error_flag(*, output: Any, **_: Any) -> Any:
        return Evaluation(name="error", value="error" in (output or {}), data_type="BOOLEAN")

    def run_scores(scorer: Callable) -> Callable:
        def evaluate(*, item_results: list, **_: Any) -> list:
            preds = {r.item.metadata["gold_id"]: r.output["output"] for r in item_results if "output" in r.output}
            result = scorer(gold, preds)
            evs = [Evaluation(name=f"{result.scorer}:{m}", value=v) for m, v in result.gate.items() if v is not None]
            return evs + [Evaluation(name=f"{result.scorer}:passed", value=result.passed, data_type="BOOLEAN")]
        return evaluate

    for r in range(repeats):
        res = await asyncio.to_thread(
            dataset.run_experiment, name=task_name, run_name=f"{base} r{r + 1}", task=make_task(r),
            evaluators=[error_flag], run_evaluators=[run_scores(s) for s in scorers], max_concurrency=concurrency,
            metadata={"gold_version": gold.version, "engine_version": engine_version, "models": ",".join(models)})
        seen = set()
        for row in res.item_results:
            gid = row.item.metadata["gold_id"]
            seen.add(gid)
            if "error" in row.output:
                errors[gid][r] = row.output["error"]
            else:
                outputs[gid][r] = row.output["output"]
        for gid in set(by_id) - seen:
            errors[gid][r] = "missing from the Langfuse run"
        if res.dataset_run_url:
            urls.append(res.dataset_run_url)
    await asyncio.to_thread(client.flush)
    items = [ItemResult(item_id=i.id, outputs=outputs[i.id], errors=errors[i.id]) for i in gold.items]
    report = EvalReport(gold_set=gold.name, gold_version=gold.version, task=task_name, engine_version=engine_version,
                        models=list(models), repeats=repeats, items=items, scores=score(gold, items, repeats, scorers),
                        run_url=urls[0] if urls else None, run_urls=urls, dataset_version=version)
    say(f"[eval] done passed={report.passed} errors={report.errors} flips={len(report.flips)}")
    return report

