"""EV-4: the eval runner -- a task over a gold set, scored, as an `EvalReport`.

`arun_eval` runs `task` on every gold item, `repeats` times, `concurrency` jobs at a time (a plain function runs off
the event loop), and streams `[eval] i/N` progress. A task that raises is an ERROR in that item's slot: never
dropped, never an answer (ADR-0133); scorers count it wrong. Results are keyed by item id. Each repeat is scored; a
scorer passes only if it passes on EVERY repeat, its reported gate is the worst value across them, and with several
repeats each repeat's gate is listed (`per_repeat_gate`) -- a pass that flips between repeats is not a pass. The
report keeps every output, so `rescore_eval` scores it again later without calling the task.

The task is where a product calls its capability, through the capability layer (`ainvoke_subgraph`,
`ainvoke_model`, or its own coroutine that does), so the eval measures what production runs. With tracing on, each
job is a span that is the current context, so the model calls it makes nest under it (EV-5).

EV-5: with `langfuse=True` (or `RAG_EVAL_BACKEND=langfuse`) the run goes through the optional Langfuse backend
(`langfuse_backend.py`): the gold set as a versioned dataset, each repeat a `run_experiment`, the scorers' gates as
run scores; the report is the same, plus the run links and the dataset version. A `local_only` gold set is refused
before anything is sent.
"""
from __future__ import annotations

import asyncio
import inspect
import os
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Awaitable, Callable, Iterable, Optional, Sequence, Union

from rag_wright.contracts.evals import EvalReport, GoldItem, GoldSet, ItemResult, ScorerResult
from rag_wright.models import tracing

Task = Callable[[GoldItem], Union[Any, Awaitable[Any]]]
Scorer = Callable[[GoldSet, dict], ScorerResult]


def _engine_version() -> str:
    try:
        return version("rag-wright")
    except PackageNotFoundError:
        return "unknown"


async def _call(task: Task, item: GoldItem) -> Any:
    if inspect.iscoroutinefunction(task) or inspect.iscoroutinefunction(getattr(task, "func", None)):
        return await task(item)
    result = await asyncio.to_thread(task, item)
    return await result if inspect.isawaitable(result) else result


def _score(gold: GoldSet, items: list[ItemResult], repeats: int, scorers: Iterable[Scorer]) -> list[ScorerResult]:
    per_repeat = [{i.item_id: i.outputs[r] for i in items if i.errors[r] is None} for r in range(repeats)]
    out = []
    for scorer in scorers:
        results = [scorer(gold, preds) for preds in per_repeat]
        first = results[0]
        gate = {m: (None if any(r.gate.get(m) is None for r in results) else min(r.gate[m] for r in results))
                for m in first.gate}
        diagnostics = dict(first.diagnostics)
        if repeats > 1:
            diagnostics["per_repeat_gate"] = [r.gate for r in results]
        out.append(first.model_copy(update={"gate": gate, "passed": all(r.passed for r in results),
                                            "diagnostics": diagnostics}))
    return out


async def arun_eval(gold: GoldSet, task: Task, scorers: Sequence[Scorer], *, repeats: int = 1,
                    concurrency: int = 8, progress: Optional[Callable[[str], Any]] = None,
                    task_name: Optional[str] = None, models: Sequence[str] = (), langfuse: Optional[bool] = None,
                    langfuse_client: Any = None, run_name: Optional[str] = None) -> EvalReport:
    """Run `task` on every item of `gold`, `repeats` times, `concurrency` jobs at a time, and score each repeat with
    `scorers` (`(gold, {item id: output}) -> ScorerResult`, e.g. `functools.partial(score_judgment, ...)`).
    `progress` receives `[eval] start N=<jobs> (...)`, `[eval] <i>/<N> <item id> r<k> ok|error: <reason>` and
    `[eval] done passed=<bool> errors=<n> flips=<n>`. `task_name` and `models` are recorded in the report.
    `langfuse` (None = `RAG_EVAL_BACKEND`, default local) runs it on the Langfuse backend, with `langfuse_client`
    (default: a client from the environment) and `run_name` (default: the task name and the time). Returns the
    `EvalReport`."""
    if repeats < 1 or concurrency < 1:
        raise ValueError("repeats and concurrency must be >= 1")
    say = progress or (lambda _line: None)
    name = task_name or getattr(task, "__name__", type(task).__name__)
    if langfuse is None:
        langfuse = os.environ.get("RAG_EVAL_BACKEND", "local").strip().lower() == "langfuse"
    if langfuse:
        if gold.local_only:
            raise ValueError(f"gold set {gold.name!r} is local_only: it is never sent to Langfuse (set local_only="
                             "False only for data that may leave the machine)")
        if langfuse_client is None:
            if not tracing._configured():
                raise RuntimeError("the Langfuse backend needs LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY")
            from langfuse import Langfuse

            langfuse_client = Langfuse()
        from rag_wright.evals.langfuse_backend import arun_on_langfuse

        return await arun_on_langfuse(gold, task, scorers, repeats=repeats, concurrency=concurrency, say=say,
                                      task_name=name, models=models, run_name=run_name, client=langfuse_client,
                                      call=_call, score=_score, engine_version=_engine_version())
    items = gold.items
    n = len(items) * repeats
    outputs: dict[str, list[Any]] = {i.id: [None] * repeats for i in items}
    errors: dict[str, list[Optional[str]]] = {i.id: [None] * repeats for i in items}
    sem = asyncio.Semaphore(concurrency)
    done = 0
    say(f"[eval] start N={n} ({len(items)} items x {repeats} repeats)")

    async def job(item: GoldItem, r: int) -> None:
        nonlocal done
        async with sem:
            try:
                with tracing.traced_item(f"eval {gold.name} {item.id} r{r + 1}",
                                         metadata={"gold_version": gold.version, "item_id": item.id}):
                    outputs[item.id][r] = await _call(task, item)
            except Exception as exc:  # noqa: BLE001 - a failed call is an error in its slot, never an answer
                errors[item.id][r] = f"{type(exc).__name__}: {exc}"
        done += 1
        err = errors[item.id][r]
        say(f"[eval] {done}/{n} {item.id} r{r + 1} " + (f"error: {err}" if err else "ok"))

    await asyncio.gather(*(job(item, r) for r in range(repeats) for item in items))
    results = [ItemResult(item_id=i.id, outputs=outputs[i.id], errors=errors[i.id]) for i in items]
    report = EvalReport(gold_set=gold.name, gold_version=gold.version,
                        task=name,
                        engine_version=_engine_version(), models=list(models), repeats=repeats, items=results,
                        scores=_score(gold, results, repeats, scorers))
    say(f"[eval] done passed={report.passed} errors={report.errors} flips={len(report.flips)}")
    return report


def run_eval(gold: GoldSet, task: Task, scorers: Sequence[Scorer], **kwargs: Any) -> EvalReport:
    """`arun_eval` from synchronous code. Inside a running event loop (a web backend), await `arun_eval`."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(arun_eval(gold, task, scorers, **kwargs))
    raise RuntimeError("run_eval was called inside a running event loop; await arun_eval")


def rescore_eval(report: EvalReport, gold: GoldSet, scorers: Sequence[Scorer]) -> EvalReport:
    """Score a saved report's outputs again (new scorers, thresholds or a corrected gold) without calling the task."""
    return report.model_copy(update={"gold_set": gold.name, "gold_version": gold.version,
                                     "scores": _score(gold, report.items, report.repeats, scorers)})
