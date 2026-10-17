"""LGI-1 (docs/specs/langgraph-ingestion/plan.md): `aingest` on the LangGraph driver does exactly what the asyncio
driver did. The record in `tests/fixtures/ingestion/parity.json` was captured from the asyncio driver BEFORE the
graph replaced it (`RAG_RECORD_PARITY=1 uv run pytest tests/ingestion/test_graph_parity.py`); every scenario must
reproduce it: the `IngestionReport`, the store's spans, nodes and edges, the document hook's calls and the
progress lines.

Hermetic: a fake store and embedder, synthetic fixtures, a deterministic extractor."""
from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import re
from pathlib import Path

import pytest

from rag_wright.api import (
    EngineConfig,
    IngestionTuning,
    IngestSource,
    KgNode,
    Span,
    StoreConfig,
    UnitExtraction,
    build_ingestion,
)
from rag_wright.api.workspace import WorkspaceHandle

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ingestion"
RECORD = FIXTURES / "parity.json"


class _Store:
    def __init__(self, fail_span_index: int | None = None):
        self.spans, self.nodes, self.edges, self.ensured = {}, [], [], []
        self._fail, self._upserts = fail_span_index, 0

    def upsert_span(self, record):
        self._upserts += 1
        if self._fail is not None and self._upserts == self._fail:
            raise RuntimeError("span store unavailable")
        self.spans[record.span_id] = record

    def kg_write(self, nodes, edges=()):
        self.nodes += list(nodes)
        self.edges += list(edges)

    def kg_ensure_edges(self, edges):
        seen = {(x.type, x.from_key, x.to_key) for x in self.ensured}
        new = [e for e in edges if (e.type, e.from_key, e.to_key) not in seen]
        self.ensured += new
        return len(new)


class _Embedder:
    def encode_batch(self, texts):
        return [[0.0] * 1024 for _ in texts], [{} for _ in texts]


async def _extractor(unit, *, source_doc_id):
    return UnitExtraction(nodes=[KgNode("Record", "record_id", {
        "record_id": f"{source_doc_id}:{unit.index}", "span_id": unit.anchor.span_id, "confidence": "EXTRACTED",
        "text": unit.text[:40]})])


async def _flaky(unit, *, source_doc_id):
    if unit.index == 1:
        raise RuntimeError("model timeout")
    if unit.index == 2:
        return UnitExtraction(nodes=[KgNode("Record", "record_id", {"record_id": "x"})])  # no provenance
    return await _extractor(unit, source_doc_id=source_doc_id)


def _overlapping(chunk_id, text, layout):  # does not tile the text
    return [Span(span_id=f"{chunk_id}#0", parent_chunk_id=chunk_id, span_index=0, start=0, end=1, text=text[:1])]


def _scenario(name: str, tmp: Path) -> dict:
    """The sources and build arguments of each scenario (built fresh in `tmp`)."""
    md, report_md = str(FIXTURES / "textile_spec_sheet.md"), str(FIXTURES / "textile_test_report.md")
    if name == "markdown":
        return dict(sources=[md])
    if name == "two_documents":
        return dict(sources=[md, report_md])
    if name == "extraction_failures":
        return dict(sources=[md], extractor=_flaky)
    if name == "broken_and_good":
        bad = tmp / "broken.xlsx"
        bad.write_bytes(b"not a workbook")
        return dict(sources=[str(bad), report_md])
    if name == "contract_breach":
        return dict(sources=[md], segmenter=_overlapping)
    if name == "span_write_failure":
        return dict(sources=[md], store=_Store(fail_span_index=2))
    # the workbooks are committed too: a freshly built OOXML file carries its build time, so its bytes (and the
    # content-hash ids of its embedded reports) change from run to run
    if name == "workbook_with_children":  # two embedded reports on the record rows of a database sheet
        return dict(sources=[IngestSource(path=str(FIXTURES / "parity_db_with_reports.xlsx"), doc_id="db",
                                          metadata={"batch": "b1"})])
    if name == "broken_child":  # one embedded report is not a document
        return dict(sources=[IngestSource(path=str(FIXTURES / "parity_db_with_a_broken_report.xlsx"), doc_id="db2")])
    # the PDFs are committed: pypdfium2 writes a random file id, so a freshly made one hashes differently each run
    if name == "pdf_attachment":
        return dict(sources=[IngestSource(path=str(FIXTURES / "parity_cover_with_attachment.pdf"), doc_id="cover")])
    if name == "blank_pdf":
        return dict(sources=[IngestSource(path=str(FIXTURES / "parity_blank.pdf"), doc_id="blank")])
    if name == "in_memory_and_record_mode":
        data = (FIXTURES / "parity_grid.xlsx").read_bytes()
        return dict(sources=[IngestSource(data=data, name="grid.xlsx", doc_id="g", table_mode="record")],
                    tuning=IngestionTuning(document_concurrency=1, extract_concurrency=1))
    raise KeyError(name)


SCENARIOS = ["markdown", "two_documents", "extraction_failures", "broken_and_good", "contract_breach",
             "span_write_failure", "workbook_with_children", "broken_child", "pdf_attachment", "blank_pdf",
             "in_memory_and_record_mode"]


def _plain(value):
    """JSON-ready, order-stable."""
    if dataclasses.is_dataclass(value):
        return _plain(dataclasses.asdict(value))
    if hasattr(value, "model_dump"):
        return _plain(value.model_dump())
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _capture(name: str, tmp: Path) -> dict:
    spec = _scenario(name, tmp)
    store = spec.pop("store", None) or _Store()
    ws = WorkspaceHandle(store, EngineConfig(store=StoreConfig(host="x", port="0", user="u", password="p")), "t")
    hook_calls: list = []

    async def hook(ws_, sd, chunks):
        mine = f"{sd.source_doc_id}:"  # its own records only: other documents run concurrently
        hook_calls.append([sd.source_doc_id, len(chunks),
                           sum(n.type == "Record" and str(n.props.get("record_id", "")).startswith(mine)
                               for n in store.nodes)])

    lines: list[str] = []
    pipe = build_ingestion(spec.pop("extractor", _extractor), embedder=_Embedder(), progress=lines.append,
                           document_hook=hook, **{k: v for k, v in spec.items() if k != "sources"})
    report = asyncio.run(pipe.aingest(ws, spec["sources"], cache_dir=tmp / "cache"))
    spans = {sid: {k: v for k, v in _plain(r).items() if k not in ("dense_vector", "sparse_vector")}
             for sid, r in store.spans.items()}
    single = len(spec["sources"]) == 1
    return {
        "report": sorted((_plain(d) for d in report.documents), key=lambda d: d["doc_id"]),
        "succeeded": report.succeeded, "failed": report.failed,
        "spans": _plain(spans),
        "nodes": sorted((_plain(n) for n in store.nodes), key=json.dumps),
        "edges": sorted((_plain(e) for e in store.edges), key=json.dumps),
        "ensured": sorted((_plain(e) for e in store.ensured), key=json.dumps),
        "hook_calls": sorted(hook_calls),
        # one document: the exact sequence. Several run concurrently, so their lines interleave and the i/N
        # counter follows completion order: compare each line without its number, and the numbers as a set
        "progress": lines if single else sorted(re.sub(r"^\[ingest\] \d+/", "[ingest] #/", ln) for ln in lines),
        "counters": sorted(int(m.group(1)) for ln in lines if (m := re.match(r"^\[ingest\] (\d+)/", ln))),
    }


@pytest.fixture(autouse=True)
def _no_vlm(monkeypatch):
    monkeypatch.setattr("rag_wright.capabilities.parsing._vlm_available", lambda: False)


@pytest.mark.parametrize("name", SCENARIOS)
def test_aingest_matches_the_recorded_asyncio_driver(name, tmp_path):
    text = json.dumps(_capture(name, tmp_path), default=str)
    for root in {str(tmp_path), str(tmp_path.resolve())}:  # a dead-letter message can name the temp path
        text = text.replace(root, "<tmp>")
    text = re.sub(r"(/private)?/var/folders/[^\s\"']+", "<tmp>", text)  # ... and docling's own temp file
    got = json.loads(text)
    if os.environ.get("RAG_RECORD_PARITY") == "1":
        record = json.loads(RECORD.read_text()) if RECORD.exists() else {}
        record[name] = got
        RECORD.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
        pytest.skip("recorded")
    expected = json.loads(RECORD.read_text())[name]
    for key in expected:
        assert got[key] == expected[key], f"{name}: {key} differs from the recorded asyncio driver"


def _workspace(store=None):
    return WorkspaceHandle(store or _Store(), EngineConfig(store=StoreConfig(host="x", port="0", user="u",
                                                                             password="p")), "t")


def test_each_document_runs_through_a_langgraph_graph_of_the_stages(tmp_path):
    from langgraph.graph.state import CompiledStateGraph

    ws = _workspace()
    pipe = build_ingestion(_extractor, embedder=_Embedder(), progress=lambda _l: None)
    graph = pipe.document_graph(ws, pipe.stages(ws, cache_dir=tmp_path))
    assert isinstance(graph, CompiledStateGraph)
    chain = ["parse", "chunk", "segment", "index", "extract", "write", "document_hook", "document_node", "children"]
    edges = {(e.source, e.target) for e in graph.get_graph().edges}
    assert {(a, b) for a, b in zip(["__start__"] + chain, chain + ["__end__"])} <= edges
    assert {(stage, "dead_letter") for stage in chain[:-1]} <= edges and ("dead_letter", "__end__") in edges


def test_a_hook_that_fails_after_the_write_dead_letters_the_document_and_keeps_its_record_count(tmp_path):
    async def failing_hook(ws, sd, chunks):
        raise RuntimeError("entity graph unavailable")

    ws = _workspace()
    pipe = build_ingestion(_extractor, embedder=_Embedder(), progress=lambda _l: None, document_hook=failing_hook)
    (doc,) = asyncio.run(pipe.aingest(ws, [str(FIXTURES / "textile_spec_sheet.md")], cache_dir=tmp_path)).documents
    assert doc.dead_letter == "RuntimeError: entity graph unavailable"
    assert doc.records == 5 and doc.spans == 0  # the records landed; the counts of a finished document are not set
    assert not any(n.type == "Document" for n in ws._store.nodes)  # it never got its Document node


# --- LGI-2: the run graph --------------------------------------------------------------------------------------------


def test_the_run_is_one_langgraph_graph_that_sends_each_source_to_the_document_graph(tmp_path):
    from langgraph.graph.state import CompiledStateGraph

    ws = _workspace()
    pipe = build_ingestion(_extractor, embedder=_Embedder(), progress=lambda _l: None)
    graph = pipe.graph(ws, cache_dir=tmp_path)
    assert isinstance(graph, CompiledStateGraph)
    out = asyncio.run(graph.ainvoke({"sources": [str(FIXTURES / "textile_spec_sheet.md"),
                                                 str(FIXTURES / "textile_test_report.md")]}))
    from rag_wright.api import IngestionReport

    report = IngestionReport.from_dict(out["report"])  # the graph's state is plain data (LGI-3)
    assert [d.doc_id for d in report.documents] == ["textile_spec_sheet_md", "textile_test_report_md"]
    assert report.succeeded == 2 and report.failed == 0


def test_the_report_lists_documents_in_source_order_whatever_order_they_finish_in(tmp_path):
    async def slow_first(unit, *, source_doc_id):
        if source_doc_id == "textile_spec_sheet_md":
            await asyncio.sleep(0.05)
        return await _extractor(unit, source_doc_id=source_doc_id)

    pipe = build_ingestion(slow_first, embedder=_Embedder(), progress=lambda _l: None)
    report = asyncio.run(pipe.aingest(_workspace(), [str(FIXTURES / "textile_spec_sheet.md"),
                                                     str(FIXTURES / "textile_test_report.md")], cache_dir=tmp_path))
    assert [d.doc_id for d in report.documents] == ["textile_spec_sheet_md", "textile_test_report_md"]


@pytest.mark.parametrize("limit", [1, 2, 3])
def test_no_more_documents_are_in_flight_than_document_concurrency(tmp_path, limit):
    in_flight, peak = 0, 0

    async def counting(unit, *, source_doc_id):
        return await _extractor(unit, source_doc_id=source_doc_id)

    async def hook(ws, sd, chunks):  # runs inside a document: count documents between parse and their hook
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1

    sources = []
    for i in range(5):
        path = tmp_path / f"doc{i}.md"
        path.write_text(f"# Sheet {i}\n\nGauge: {20 + i}G.\n")
        sources.append(str(path))
    pipe = build_ingestion(counting, embedder=_Embedder(), progress=lambda _l: None, document_hook=hook,
                           tuning=IngestionTuning(document_concurrency=limit))
    report = asyncio.run(pipe.aingest(_workspace(), sources, cache_dir=tmp_path / "cache"))
    assert report.succeeded == 5 and peak == limit


def test_the_progress_lines_bracket_the_run(tmp_path):
    lines: list[str] = []
    pipe = build_ingestion(_extractor, embedder=_Embedder(), progress=lines.append)
    asyncio.run(pipe.aingest(_workspace(), [str(FIXTURES / "textile_spec_sheet.md"),
                                            str(FIXTURES / "textile_test_report.md")], cache_dir=tmp_path))
    assert lines[0] == "[ingest] start N=2" and lines[-1] == "[ingest] done 2 ok, 0 dead-lettered"
    assert sorted(ln.split()[1] for ln in lines[1:-1]) == ["1/2", "2/2"]


def test_a_run_with_no_sources_reports_nothing(tmp_path):
    lines: list[str] = []
    pipe = build_ingestion(_extractor, embedder=_Embedder(), progress=lines.append)
    report = asyncio.run(pipe.aingest(_workspace(), [], cache_dir=tmp_path))
    assert report.documents == [] and lines == ["[ingest] start N=0", "[ingest] done 0 ok, 0 dead-lettered"]


# --- LGI-3: resuming an interrupted run from a checkpoint ------------------------------------------------------------


class _Crash(BaseException):
    """Stands in for the process dying mid-run (a BaseException is never dead-lettered)."""


class _CountingEmbedder(_Embedder):
    def __init__(self):
        self.batches = 0

    def encode_batch(self, texts):
        self.batches += 1
        return super().encode_batch(texts)


def _three_sources(tmp_path) -> list[str]:
    out = []
    for i in range(3):
        path = tmp_path / f"sheet{i}.md"
        path.write_text(f"# Sheet {i}\n\nGauge: {20 + i}G.\n\nStitch length: 2.{i} mm.\n")
        out.append(str(path))
    return out


def _crashing_extractor(calls: list, crash_on: set):
    async def extract(unit, *, source_doc_id):
        calls.append(source_doc_id)
        if source_doc_id in crash_on:
            crash_on.discard(source_doc_id)
            raise _Crash()
        return await _extractor(unit, source_doc_id=source_doc_id)
    return extract


def _saver(strict: bool):
    """An in-memory saver; `strict` = LangGraph's strict msgpack mode, which loads built-in types only."""
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    return InMemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=None)) if strict else InMemorySaver()


@pytest.mark.parametrize("strict", [False, True], ids=["default-serde", "strict-serde"])
def test_a_crashed_run_resumes_where_it_stopped_and_reports_as_if_it_never_crashed(tmp_path, strict):

    sources = _three_sources(tmp_path)
    one_at_a_time = IngestionTuning(document_concurrency=1, extract_concurrency=1)
    clean_ws = _workspace()
    clean = asyncio.run(build_ingestion(_extractor, embedder=_Embedder(), progress=lambda _l: None,
                                        tuning=one_at_a_time).aingest(clean_ws, sources, cache_dir=tmp_path / "c1"))

    calls, crash_on, embedder, saver, ws = [], {"sheet1_md"}, _CountingEmbedder(), _saver(strict), _workspace()
    pipe = build_ingestion(_crashing_extractor(calls, crash_on), embedder=embedder, progress=lambda _l: None,
                           tuning=one_at_a_time)
    with pytest.raises(_Crash):
        asyncio.run(pipe.aingest(ws, sources, cache_dir=tmp_path / "c2", checkpointer=saver, run_id="r1"))
    assert embedder.batches == 2 and "sheet2_md" not in calls  # sheet0 done, sheet1 indexed then crashed

    calls.clear()
    embedder.batches = 0
    report = asyncio.run(pipe.aingest(ws, sources, cache_dir=tmp_path / "c2", checkpointer=saver, run_id="r1"))
    assert "sheet0_md" not in calls and "sheet1_md" in calls and "sheet2_md" in calls  # finished work not redone
    assert embedder.batches == 1  # only sheet2 is indexed: sheet1's index stage had finished before the crash
    assert [_plain(d) for d in report.documents] == [_plain(d) for d in clean.documents]
    assert sorted(map(json.dumps, map(_plain, ws._store.nodes))) == \
        sorted(map(json.dumps, map(_plain, clean_ws._store.nodes)))


def test_the_progress_of_a_resumed_run_continues_its_count(tmp_path):
    from langgraph.checkpoint.memory import InMemorySaver

    sources, saver, lines = _three_sources(tmp_path), InMemorySaver(), []
    pipe = build_ingestion(_crashing_extractor([], {"sheet1_md"}), embedder=_Embedder(), progress=lines.append,
                           tuning=IngestionTuning(document_concurrency=1, extract_concurrency=1))
    with pytest.raises(_Crash):
        asyncio.run(pipe.aingest(_workspace(), sources, cache_dir=tmp_path, checkpointer=saver, run_id="r1"))
    lines.clear()
    asyncio.run(pipe.aingest(_workspace(), sources, cache_dir=tmp_path, checkpointer=saver, run_id="r1"))
    assert lines[0] == "[ingest] resume r1: 1/3 documents done"
    assert [ln.split()[1] for ln in lines[1:-1]] == ["2/3", "3/3"] and lines[-1].startswith("[ingest] done 3 ok")


def test_a_finished_run_is_not_run_again(tmp_path):
    from langgraph.checkpoint.memory import InMemorySaver

    sources, saver = _three_sources(tmp_path), InMemorySaver()
    pipe = build_ingestion(_extractor, embedder=_Embedder(), progress=lambda _l: None)
    asyncio.run(pipe.aingest(_workspace(), sources, cache_dir=tmp_path, checkpointer=saver, run_id="r1"))
    with pytest.raises(ValueError, match="already finished"):
        asyncio.run(pipe.aingest(_workspace(), sources, cache_dir=tmp_path, checkpointer=saver, run_id="r1"))


def test_a_run_resumes_only_with_the_sources_it_started_with(tmp_path):
    from langgraph.checkpoint.memory import InMemorySaver

    sources, saver = _three_sources(tmp_path), InMemorySaver()
    pipe = build_ingestion(_crashing_extractor([], {"sheet1_md"}), embedder=_Embedder(), progress=lambda _l: None,
                           tuning=IngestionTuning(document_concurrency=1))
    with pytest.raises(_Crash):
        asyncio.run(pipe.aingest(_workspace(), sources, cache_dir=tmp_path, checkpointer=saver, run_id="r1"))
    with pytest.raises(ValueError, match="different sources"):
        asyncio.run(pipe.aingest(_workspace(), sources[:2], cache_dir=tmp_path, checkpointer=saver, run_id="r1"))


def test_a_checkpointer_and_a_run_id_come_together(tmp_path):
    from langgraph.checkpoint.memory import InMemorySaver

    pipe = build_ingestion(_extractor, embedder=_Embedder(), progress=lambda _l: None)
    with pytest.raises(ValueError, match="run_id"):
        asyncio.run(pipe.aingest(_workspace(), [], cache_dir=tmp_path, checkpointer=InMemorySaver()))
    with pytest.raises(ValueError, match="checkpointer"):
        asyncio.run(pipe.aingest(_workspace(), [], cache_dir=tmp_path, run_id="r1"))


def test_a_run_that_crashed_inside_an_embedded_report_resumes_inside_it(tmp_path):
    source = [IngestSource(path=str(FIXTURES / "parity_db_with_reports.xlsx"), doc_id="db")]
    clean_ws = _workspace()
    clean = asyncio.run(build_ingestion(_extractor, embedder=_Embedder(), progress=lambda _l: None)
                        .aingest(clean_ws, source, cache_dir=tmp_path / "c1"))
    second_child = clean.documents[2].doc_id  # the parent, then its two reports, in order

    calls, saver, ws = [], _saver(strict=True), _workspace()
    pipe = build_ingestion(_crashing_extractor(calls, {second_child}), embedder=_Embedder(),
                           progress=lambda _l: None)
    with pytest.raises(_Crash):
        asyncio.run(pipe.aingest(ws, source, cache_dir=tmp_path / "c2", checkpointer=saver, run_id="r1"))
    calls.clear()
    report = asyncio.run(pipe.aingest(ws, source, cache_dir=tmp_path / "c2", checkpointer=saver, run_id="r1"))
    assert set(calls) == {second_child}  # the parent and the first report are not extracted again
    assert [_plain(d) for d in report.documents] == [_plain(d) for d in clean.documents]
    assert sorted(map(json.dumps, map(_plain, ws._store.ensured))) == \
        sorted(map(json.dumps, map(_plain, clean_ws._store.ensured)))
