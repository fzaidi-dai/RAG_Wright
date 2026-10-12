"""EV-1: `evaluate_ingestion` ready for a web backend -- `aevaluate_ingestion` runs inside a running event loop, runs
documents `tuning.document_concurrency` at a time, streams `i/N` progress, and the result serialises to JSON with
`passed` in it."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

import pytest

from rag_wright.api import IngestionTuning, aevaluate_ingestion, evaluate_ingestion
from rag_wright.ingestion.segment import segment_layout

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ingestion"
SAMPLES = [str(FIXTURES / "textile_spec_sheet.md"), str(FIXTURES / "textile_test_report.md")]


def test_the_async_form_matches_the_sync_form(tmp_path):
    sync = evaluate_ingestion(SAMPLES, cache_dir=tmp_path / "a")
    async_ = asyncio.run(aevaluate_ingestion(SAMPLES, cache_dir=tmp_path / "b"))
    assert async_.to_dict() == sync.to_dict()
    assert [d.doc for d in async_.documents] == ["textile_spec_sheet.md", "textile_test_report.md"]


def test_progress_streams_start_each_document_and_done(tmp_path):
    lines: list[str] = []
    asyncio.run(aevaluate_ingestion(SAMPLES, cache_dir=tmp_path, progress=lines.append))
    assert lines[0] == "[evaluate] start N=2"
    assert sorted(ln.split()[1] for ln in lines[1:3]) == ["1/2", "2/2"]
    assert all(ln.endswith(" ok") for ln in lines[1:3])
    assert lines[-1] == "[evaluate] done passed=True failures=0"


def test_a_document_that_fails_is_reported_in_progress_and_failures(tmp_path):
    lines: list[str] = []
    ev = asyncio.run(aevaluate_ingestion([SAMPLES[0], str(tmp_path / "missing.md")], cache_dir=tmp_path,
                                         progress=lines.append))
    assert [d.doc for d in ev.documents] == ["textile_spec_sheet.md", "missing.md"]
    assert any(ln.startswith("[evaluate] ") and "missing.md error: FileNotFoundError" in ln for ln in lines)
    assert not ev.passed and any(f.startswith("missing.md: FileNotFoundError") for f in ev.failures)
    assert lines[-1] == f"[evaluate] done passed=False failures={len(ev.failures)}"


def test_the_result_is_json_ready_and_carries_passed(tmp_path):
    gold = {"textile_spec_sheet.md": [{"text": "Gauge", "label": ""}]}
    ev = evaluate_ingestion(SAMPLES, cache_dir=tmp_path, unit_labels=gold)
    out = json.loads(json.dumps(ev.to_dict()))
    assert out["passed"] is ev.passed
    assert out["failures"] == ev.failures
    assert out["documents"][0]["doc"] == "textile_spec_sheet.md"
    assert out["labels"]["gold"] == ev.labels.gold


def _counting_segmenter():
    live, peak, lock = [0], [0], threading.Lock()

    def segment(cid, text, layout):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.05)
        with lock:
            live[0] -= 1
        return segment_layout(cid, text, layout, tuning=IngestionTuning())

    return segment, peak


@pytest.mark.parametrize("concurrency", [1, 2])
def test_documents_run_document_concurrency_at_a_time(tmp_path, concurrency):
    segment, peak = _counting_segmenter()
    asyncio.run(aevaluate_ingestion(SAMPLES * 2, cache_dir=tmp_path, segmenter=segment,
                                    tuning=IngestionTuning(document_concurrency=concurrency)))
    assert peak[0] == concurrency


def test_the_async_form_leaves_the_event_loop_free(tmp_path):
    async def run():
        ticks = 0

        async def heartbeat():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        hb = asyncio.create_task(heartbeat())
        segment, _ = _counting_segmenter()
        await aevaluate_ingestion(SAMPLES, cache_dir=tmp_path, segmenter=segment,
                                  tuning=IngestionTuning(document_concurrency=1))
        hb.cancel()
        return ticks

    assert asyncio.run(run()) >= 5  # the parse and segment steps run off the loop


def test_the_sync_form_inside_a_running_loop_names_the_async_form(tmp_path):
    async def call():
        evaluate_ingestion(SAMPLES, cache_dir=tmp_path)

    with pytest.raises(RuntimeError, match="aevaluate_ingestion"):
        asyncio.run(call())
