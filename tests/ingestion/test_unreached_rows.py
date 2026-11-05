"""PS-62 (TexWright, 2026-11-05): rows of a record table that reached no unit with their `TableRow` were lost
silently -- every check passed and the ingest reported success. The report now names each such table: how many of
its data rows reached a unit, and which did not. A block table (one unit, no row units) is not a loss."""
from __future__ import annotations

import asyncio

import openpyxl

from rag_wright.api import IngestSource, IngestionReport, KgNode, UnitExtraction, build_ingestion
from rag_wright.capabilities import rlm_chunking as rc
from rag_wright.ingestion.builder import ExtractStage, _dump_extract_stage, _load_extract_stage

from tests.ingestion.test_builder import _FakeEmbedder, _ws


def _sheet(tmp_path, rows: int, cols: int):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Results"
    ws.append(["Sr#", "SDP"] + [f"Column {c}" for c in range(cols - 2)])
    for i in range(1, rows + 1):
        ws.append([i, 3000 + i] + [f"value {i}-{c}" for c in range(cols - 2)])
    path = tmp_path / f"sheet_{rows}x{cols}.xlsx"
    wb.save(path)
    return path


async def _extract(unit, *, source_doc_id):
    return UnitExtraction(nodes=[KgNode("Record", "record_id", {"record_id": f"{source_doc_id}:{unit.index}"})])


def _ingest(tmp_path, path, table_mode="record"):
    lines: list[str] = []
    pipe = build_ingestion(_extract, embedder=_FakeEmbedder(), progress=lines.append)
    report = asyncio.run(pipe.aingest(_ws(), [IngestSource(path=str(path), doc_id="db", table_mode=table_mode)],
                                      cache_dir=tmp_path / "cache"))
    return report.documents[0], lines


def test_rows_that_reach_no_unit_are_reported_not_silent(tmp_path, monkeypatch):
    def old_cut(text, token_cap):  # the pre-PS-60 fixed-length cut, which lost the rows past it
        cap = token_cap * 4
        return [text[i: i + cap] for i in range(0, len(text), cap)]

    monkeypatch.setattr(rc, "_hard_split", old_cut)
    doc, lines = _ingest(tmp_path, _sheet(tmp_path, 140, 76))
    assert doc.dead_letter is None  # the document still lands
    (table,) = doc.unreached_table_rows
    assert table["sheet"] == "Results" and table["total"] == 140 and 0 < table["reached"] < 140
    assert len(table["missing"]) == 140 - table["reached"] and table["missing"][-1] == 140
    assert any(ln.startswith("[ingest] warning:") and "140" in ln for ln in lines)


def test_a_table_whose_every_row_reached_a_unit_reports_nothing(tmp_path):
    doc, lines = _ingest(tmp_path, _sheet(tmp_path, 140, 76))
    assert doc.unreached_table_rows == [] and not any("warning:" in ln for ln in lines)


def test_a_block_table_is_not_a_loss(tmp_path):
    doc, _ = _ingest(tmp_path, _sheet(tmp_path, 3, 3), table_mode="block")
    assert doc.units == 1 and doc.unreached_table_rows == []


def test_the_finding_survives_the_graph_state_and_the_report_round_trip():
    finding = {"table_ref": "#/tables/0", "sheet": "Results", "reached": 1, "total": 2, "missing": [2]}
    stage = _load_extract_stage(_dump_extract_stage(ExtractStage(unreached_rows=[finding])))
    assert stage.unreached_rows == [finding]
    old = _dump_extract_stage(ExtractStage())
    del old["unreached_rows"]  # a run checkpointed before PS-62 resumes
    assert _load_extract_stage(old).unreached_rows == []
    report = IngestionReport.from_dict({"documents": [{"doc_id": "db", "unreached_table_rows": [finding]}]})
    assert report.documents[0].unreached_table_rows == [finding]
