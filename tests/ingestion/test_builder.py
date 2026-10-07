"""ING-4b (ADR-0124): the public `build_ingestion` builder -- engine-owned wiring around the domain's hooks.

Hermetic: a fake store and embedder, synthetic fixtures (markdown + a workbook with embedded reports), and a
deterministic extractor (one record node per unit)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from rag_wright.api import (
    EngineConfig,
    EngineOptions,
    IngestionTuning,
    IngestOptions,
    IngestSource,
    KgNode,
    StoreConfig,
    UnitExtraction,
    build_ingestion,
)
from rag_wright.api.workspace import WorkspaceHandle

from tests.corpus._ooxml_fixtures import make_xlsx, packager

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ingestion"


class _FakeStore:
    def __init__(self):
        self.spans, self.nodes, self.edges, self.ensured = {}, [], [], []

    def upsert_span(self, record):
        self.spans[record.span_id] = record

    def kg_write(self, nodes, edges=()):
        self.nodes += list(nodes)
        self.edges += list(edges)

    def kg_ensure_edges(self, edges):
        new = [e for e in edges if (e.type, e.from_key, e.to_key) not in {(x.type, x.from_key, x.to_key)
                                                                         for x in self.ensured}]
        self.ensured += new
        return len(new)


class _FakeEmbedder:
    def encode_batch(self, texts):
        return [[0.0] * 1024 for _ in texts], [{} for _ in texts]


def _ws(options=None):
    cfg = EngineConfig(store=StoreConfig(host="x", port="0", user="u", password="p"),
                       options=options or EngineOptions())
    return WorkspaceHandle(_FakeStore(), cfg, "test")


async def _record_extractor(unit, *, source_doc_id):
    return UnitExtraction(nodes=[KgNode("Record", "record_id", {
        "record_id": f"{source_doc_id}:{unit.index}", "span_id": unit.anchor.span_id, "confidence": "EXTRACTED",
        "text": unit.text[:40]})])


def _run(ws, sources, tmp_path, **kw):
    lines = []
    pipe = build_ingestion(_record_extractor, embedder=_FakeEmbedder(), progress=lines.append, **kw)
    report = asyncio.run(pipe.aingest(ws, sources, cache_dir=tmp_path / "cache"))
    return report, lines


def test_a_document_runs_end_to_end(tmp_path):
    ws = _ws()
    report, lines = _run(ws, [str(FIXTURES / "textile_spec_sheet.md")], tmp_path)
    (doc,) = report.documents
    assert doc.dead_letter is None and doc.units == 5 and doc.records == 5 and doc.spans == len(ws._store.spans)
    assert {n.type for n in ws._store.nodes} == {"Record", "Document"}
    assert any(n.type == "Document" and n.props["doc_id"] == doc.doc_id for n in ws._store.nodes)
    assert any(line.startswith("[ingest] 1/1 ") for line in lines)


def test_extractor_failures_are_recorded_not_fatal(tmp_path):
    async def flaky(unit, *, source_doc_id):
        if unit.index == 1:
            raise RuntimeError("model timeout")
        if unit.index == 2:
            return UnitExtraction(nodes=[KgNode("Record", "record_id", {"record_id": "x"})])  # no provenance
        return await _record_extractor(unit, source_doc_id=source_doc_id)

    ws = _ws()
    pipe = build_ingestion(flaky, embedder=_FakeEmbedder(), progress=lambda _l: None)
    (doc,) = asyncio.run(pipe.aingest(ws, [str(FIXTURES / "textile_spec_sheet.md")], cache_dir=tmp_path)).documents
    assert doc.dead_letter is None and doc.records == 3
    reasons = sorted(f["reason"] for f in doc.extraction_failures)
    assert len(reasons) == 2 and any("model timeout" in r for r in reasons) and any("span_id" in r for r in reasons)


def test_a_broken_document_is_dead_lettered_and_the_rest_ingest(tmp_path):
    bad = tmp_path / "broken.xlsx"
    bad.write_bytes(b"not a workbook")
    report, _ = _run(_ws(), [str(bad), str(FIXTURES / "textile_test_report.md")], tmp_path)
    by_id = {d.doc_id: d for d in report.documents}
    assert by_id["broken_xlsx"].dead_letter and by_id["textile_test_report_md"].dead_letter is None
    assert report.failed == 1 and report.succeeded == 1


def test_a_hook_override_is_held_to_the_contract(tmp_path):
    from rag_wright.api import Span

    def overlapping(chunk_id, text, layout):  # does not tile the text
        return [Span(span_id=f"{chunk_id}#0", parent_chunk_id=chunk_id, span_index=0, start=0, end=1, text=text[:1])]

    report, _ = _run(_ws(), [str(FIXTURES / "textile_spec_sheet.md")], tmp_path, segmenter=overlapping)
    assert "tile" in report.documents[0].dead_letter


_DB = [["Sr#", "Sample", "Standard", "Test"],
       [1, "SDP 3101", "EN 17092", "Tear"], [1, "SDP 3101", "EN 17092", "Abrasion"],
       [2, "SDP 3102", "EN 17092", "Tear"], [2, "SDP 3102", "EN 17092", "Abrasion"],
       [3, "SDP 3103", "EN 17092", "Tear"], [3, "SDP 3103", "EN 17092", "Abrasion"]]


def _report_docx(tmp_path: Path, name: str, text: str) -> bytes:
    from tests.corpus._ooxml_fixtures import make_docx

    return make_docx(tmp_path / name, ["Test report", text], {}).read_bytes() + b"\0" * 4096  # >= one CFB block


def test_embedded_children_are_ingested_and_attached_to_their_record_rows(tmp_path):
    r1 = _report_docx(tmp_path, "r1.docx", "Tear strength results for the 3101 trial.")
    r3 = _report_docx(tmp_path, "r3.docx", "Abrasion results for the 3103 trial.")
    x = make_xlsx(tmp_path / "db.xlsx", _DB, [
        (3, 1, "oleObject1.bin", packager("SDP 3101 report.docx", r1)),   # on its own row
        (3, 1, "oleObject2.bin", packager("SDP 3103 report.docx", r3)),   # stacked, belongs to 3103
    ])
    ws = _ws()
    report, _ = _run(ws, [IngestSource(path=str(x), doc_id="db")], tmp_path)
    parent = next(d for d in report.documents if d.doc_id == "db")
    assert len(parent.children) == 2 and parent.dead_letter is None
    docs = {n.props["doc_id"]: n.props for n in ws._store.nodes if n.type == "Document"}
    assert {c for c in docs if c.startswith("db.emb.")} == set(parent.children)
    assert all(docs[c]["parent_doc_id"] == "db" for c in parent.children)
    edges = ws._store.ensured
    assert sum(e.type == "EmbeddedIn" for e in edges) == 2
    spans = ws._store.spans
    attached = {(e.from_key, spans[e.to_key].text.strip(), e.props["confidence"]) for e in edges
                if e.type == "AttachedTo"}
    c3101 = next(c for c in parent.children if docs[c]["filename"] == "SDP 3101 report.docx")
    c3103 = next(c for c in parent.children if docs[c]["filename"] == "SDP 3103 report.docx")
    assert (c3101, "| 1 | SDP 3101 | EN 17092 | Tear |", "EXTRACTED") in attached
    assert (c3103, "| 3 | SDP 3103 | EN 17092 | Abrasion |", "INFERRED") in attached
    assert parent.links == {"EXTRACTED": 1, "INFERRED": 3}


def test_table_mode_comes_from_the_source(tmp_path):
    rows = [["Parameter", "Proposed", "Actual"], ["Gauge", "28G", "28G"], ["Stitch", "2.8", "2.9"]]
    x = make_xlsx(tmp_path / "grid.xlsx", rows, [])
    auto, _ = _run(_ws(), [IngestSource(path=str(x), doc_id="g1")], tmp_path)
    forced, _ = _run(_ws(), [IngestSource(path=str(x), doc_id="g2", table_mode="record")], tmp_path)
    assert auto.documents[0].units == 1 and forced.documents[0].units == 2


def test_tuning_comes_from_the_engine_config(tmp_path):
    options = EngineOptions(ingest=IngestOptions(tuning=IngestionTuning(max_unit_chars=100)))
    report, _ = _run(_ws(options), [str(FIXTURES / "textile_spec_sheet.md")], tmp_path)
    assert report.documents[0].units > 5


@pytest.mark.parametrize("bad", [0, -1])
def test_concurrency_must_be_positive(bad):
    with pytest.raises(ValueError):
        build_ingestion(_record_extractor, tuning=IngestionTuning(extract_concurrency=bad))


def test_files_with_the_same_name_but_different_types_get_distinct_ids(tmp_path):
    """A PDF export and its spreadsheet original share a name: their derived document ids must not collide."""
    md = tmp_path / "report.md"
    md.write_text("# Report\n\nGauge: 28G.\n")
    txt = tmp_path / "report.txt"
    txt.write_text("Gauge: 28G.\n")
    ws = _ws()
    report, _ = _run(ws, [str(md), str(txt)], tmp_path)
    ids = sorted(d.doc_id for d in report.documents)
    assert len(set(ids)) == 2 and all(d.dead_letter is None for d in report.documents), ids
    assert len({n.props["doc_id"] for n in ws._store.nodes if n.type == "Document"}) == 2


@pytest.fixture
def no_vlm(monkeypatch):
    """A blank page goes through tiered OCR; keep it local (never a real VLM call from a unit test)."""
    monkeypatch.setattr("rag_wright.capabilities.parsing._vlm_available", lambda: False)


def test_a_pdf_attachment_is_ingested_as_a_child(tmp_path, no_vlm):
    import io

    import pypdfium2

    doc = pypdfium2.PdfDocument.new()
    doc.new_page(200, 200)
    doc.new_attachment("results.docx").set_data(_report_docx(tmp_path, "att.docx", "Bursting strength 412 kPa."))
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    pdf = tmp_path / "cover.pdf"
    pdf.write_bytes(buf.getvalue())
    ws = _ws()
    report, _ = _run(ws, [IngestSource(path=str(pdf), doc_id="cover")], tmp_path)
    parent = next(d for d in report.documents if d.doc_id == "cover")
    (child_id,) = parent.children
    assert any(e.type == "EmbeddedIn" and e.from_key == child_id and e.to_key == "cover" for e in ws._store.ensured)
    assert not any(e.type == "AttachedTo" for e in ws._store.ensured)  # no table row to attach to


def test_a_document_with_no_text_is_ingested_empty_not_dead_lettered(tmp_path, no_vlm):
    import pypdfium2

    doc = pypdfium2.PdfDocument.new()
    doc.new_page(200, 200)  # a blank page: nothing to chunk
    pdf = tmp_path / "blank.pdf"
    doc.save(str(pdf))
    doc.close()
    ws = _ws()
    report, _ = _run(ws, [IngestSource(path=str(pdf), doc_id="blank")], tmp_path)
    (rep,) = report.documents
    assert rep.dead_letter is None and rep.chunks == 0 and rep.spans == 0 and rep.units == 0
    assert any(n.type == "Document" and n.props["doc_id"] == "blank" for n in ws._store.nodes)
