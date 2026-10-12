"""EV-5: the optional Langfuse backend -- the gold set as a versioned dataset, each repeat a `run_experiment`, the
scorers' gates pushed as run scores -- and model calls nested under their eval item (local runner too).

Hermetic: a fake client with the behaviour grounded against Langfuse 4.15.1 in EV-0 / EV-5 (a dataset create updates
in place; items carry a server `updated_at`; `run_experiment` drops a task that raises; the task gets a
`DatasetItem`)."""
from __future__ import annotations

import asyncio
import datetime as dt
import functools
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from rag_wright.api import GoldItem, GoldSet, arun_eval, score_classification
from rag_wright.models import tracing

GOLD = GoldSet(name="parity", version="v2", local_only=False,
               items=[GoldItem(id=f"i{n}", input=n, expected="even" if n % 2 == 0 else "odd") for n in range(4)])
CLS = functools.partial(score_classification, floor=0.5)


async def parity(item):
    return "even" if item.input % 2 == 0 else "odd"


class _FakeDataset:
    def __init__(self, lf, name, version):
        self._lf, self.name, self.version = lf, name, version
        self.items = [SimpleNamespace(**it) for it in lf.items.values()
                      if it["dataset_name"] == name and it["status"] != "ARCHIVED"]

    def run_experiment(self, *, name, run_name=None, description=None, task, evaluators=(), run_evaluators=(),
                       max_concurrency=50, metadata=None, **_):
        self._lf.runs.append({"name": name, "run_name": run_name, "version": self.version, "metadata": metadata})

        async def go():
            results = []
            for it in self.items:
                try:
                    out = await task(item=it)
                except Exception:  # noqa: BLE001 - the real SDK drops the item (EV-0 finding 2)
                    continue
                evs = [e for ev in evaluators for e in [ev(input=it.input, output=out,
                                                          expected_output=it.expected_output, metadata=it.metadata)]]
                results.append(SimpleNamespace(item=it, output=out, evaluations=evs, trace_id="t"))
            run_evs = [e for rev in run_evaluators for e in (lambda r: r if isinstance(r, list) else [r])(
                rev(item_results=results))]
            self._lf.run_scores.append({e.name: e.value for e in run_evs})
            return SimpleNamespace(item_results=results, run_evaluations=run_evs,
                                   dataset_run_url=f"http://lf/runs/{run_name}", dataset_run_id=run_name)

        return asyncio.run(go())


class FakeLangfuse:
    def __init__(self):
        self.datasets, self.items, self.runs, self.run_scores = {}, {}, [], []
        self._clock = dt.datetime(2026, 10, 12, tzinfo=dt.timezone.utc)

    def create_dataset(self, *, name, description=None, metadata=None, **_):
        self.datasets[name] = {"description": description, "metadata": metadata}
        return SimpleNamespace(name=name)

    def create_dataset_item(self, *, dataset_name, id, input=None, expected_output=None, metadata=None,
                            status=None, **_):
        self._clock += dt.timedelta(seconds=1)
        self.items[id] = {"dataset_name": dataset_name, "id": id, "input": input, "expected_output": expected_output,
                          "metadata": metadata, "status": status or "ACTIVE", "updated_at": self._clock}
        return SimpleNamespace(**self.items[id])

    def get_dataset(self, name, *, version=None, **_):
        return _FakeDataset(self, name, version)

    def flush(self):
        pass


def _run(**kw):
    return asyncio.run(arun_eval(GOLD, kw.pop("task", parity), [CLS], langfuse=True,
                                 langfuse_client=kw.pop("client"), **kw))


def test_the_gold_set_becomes_a_versioned_dataset_and_each_repeat_a_run():
    lf = FakeLangfuse()
    report = _run(client=lf, repeats=2, run_name="nightly")
    assert sorted(lf.items) == [f"parity:i{n}" for n in range(4)]
    assert lf.items["parity:i0"]["metadata"]["gold_id"] == "i0" and lf.datasets["parity"]["metadata"]["gold_version"] == "v2"
    assert [r["run_name"] for r in lf.runs] == ["nightly r1", "nightly r2"]
    assert report.passed is True and report.items[1].outputs == ["odd", "odd"]
    assert report.run_urls == ["http://lf/runs/nightly r1", "http://lf/runs/nightly r2"]
    assert report.run_url == report.run_urls[0] and report.dataset_version == "2026-10-12T00:00:04+00:00"
    assert lf.runs[0]["version"] == dt.datetime(2026, 10, 12, 0, 0, 4, tzinfo=dt.timezone.utc)


def test_a_failed_task_is_an_error_in_its_slot_never_dropped():
    async def flaky(item):
        if item.id == "i2":
            raise TimeoutError("182 s")
        return await parity(item)

    report = _run(client=FakeLangfuse(), task=flaky)
    assert report.items[2].errors == ["TimeoutError: 182 s"] and report.errors == 1
    assert report.scores[0].diagnostics["errors"] == 1


def test_the_scorers_gates_are_pushed_as_run_scores():
    lf = FakeLangfuse()
    _run(client=lf)
    assert lf.run_scores == [{"classification:min_class_recall@1": 1.0, "classification:passed": True}]


def test_items_dropped_from_the_gold_set_are_archived():
    lf = FakeLangfuse()
    lf.create_dataset_item(dataset_name="parity", id="parity:old", input=9, expected_output="odd",
                           metadata={"gold_id": "old"})
    report = _run(client=lf)
    assert lf.items["parity:old"]["status"] == "ARCHIVED" and [i.item_id for i in report.items] == ["i0", "i1", "i2", "i3"]


def test_a_local_only_gold_set_is_refused_before_anything_is_sent():
    lf = FakeLangfuse()
    local = GOLD.model_copy(update={"local_only": True})
    with pytest.raises(ValueError, match="local_only"):
        asyncio.run(arun_eval(local, parity, [CLS], langfuse=True, langfuse_client=lf))
    assert lf.items == {} and lf.runs == []


def test_the_backend_is_opt_in(monkeypatch):
    monkeypatch.delenv("RAG_EVAL_BACKEND", raising=False)
    lf = FakeLangfuse()
    asyncio.run(arun_eval(GOLD, parity, [CLS], langfuse_client=lf))
    assert lf.runs == []  # default: local
    monkeypatch.setenv("RAG_EVAL_BACKEND", "langfuse")
    asyncio.run(arun_eval(GOLD, parity, [CLS], langfuse_client=lf))
    assert len(lf.runs) == 1


def test_asking_for_langfuse_without_configuration_fails_clearly(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="LANGFUSE_PUBLIC_KEY"):
        asyncio.run(arun_eval(GOLD, parity, [CLS], langfuse=True))


def test_progress_is_the_same_on_the_backend():
    lines: list[str] = []
    _run(client=FakeLangfuse(), progress=lines.append)
    assert lines[0] == "[eval] start N=4 (4 items x 1 repeats)" and lines[-1].startswith("[eval] done passed=True")
    assert len([ln for ln in lines if "/4 " in ln]) == 4


def test_the_local_runner_opens_a_current_span_per_item_so_model_calls_nest(monkeypatch):
    opened: list[str] = []

    @contextmanager
    def fake_item(name, *, metadata=None):
        opened.append(name)
        yield

    monkeypatch.setattr(tracing, "traced_item", fake_item)
    asyncio.run(arun_eval(GOLD, parity, [CLS], repeats=2))
    assert sorted(opened) == sorted(f"eval parity i{n} r{r}" for n in range(4) for r in (1, 2))
