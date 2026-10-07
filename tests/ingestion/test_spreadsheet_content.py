"""ING-4a (ADR-0124): spreadsheet content -- hidden sheets included by default (optional skip, reported), compact
table text for spreadsheet sources, and database-style tables grouped one record per row.

Hermetic: the workbooks are built here with openpyxl (docling's own spreadsheet dependency) and parsed by docling's
spreadsheet backend, which uses no model."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from rag_wright.api import TaggedSpan, check_units, parse_document
from rag_wright.ingestion import group_units, segment_layout


def _workbook(path: Path) -> Path:
    import openpyxl

    wb = openpyxl.Workbook()
    db = wb.active
    db.title = "Results"
    db.append(["Sr#", "Sample", "Customer", "Standard", "Test", "Level", "GSM", "Lab"])
    for i in range(1, 7):
        db.append([i, f"SDP {3100 + i}", "Northwind", "EN 17092", "Tear" if i % 2 else "Abrasion", "AA", 380 + i,
                   "Lab A"])
    hidden = wb.create_sheet("Trial log")
    hidden.append(["Sr.no", "SDF", "Yarn details", "Findings"])
    hidden.append([1, 2098, "150 DN polyester + 40 DN lycra", "more stretch than the 75+40 trial"])
    hidden.sheet_state = "hidden"
    wb.save(path)
    return path


def _parse(tmp_path, **kw):
    return parse_document("wb1", _workbook(tmp_path / "results.xlsx"), cache_dir=tmp_path / "cache", **kw)


def _text_of(src) -> str:
    from rag_wright.capabilities.parsing import load_document
    from rag_wright.corpus.document_parser import content_items

    return "\n\n".join(it.text for it in content_items(load_document(src.parsed)))


def test_hidden_sheets_are_included_by_default(tmp_path):
    src = _parse(tmp_path)
    assert "more stretch than the 75+40 trial" in _text_of(src)
    assert src.skipped_hidden_sheets == []


def test_hidden_sheets_can_be_skipped_and_the_skip_is_reported(tmp_path):
    src = _parse(tmp_path, include_hidden_sheets=False)
    assert "more stretch than the 75+40 trial" not in _text_of(src)
    assert src.skipped_hidden_sheets == ["Trial log"]  # nothing disappears unseen


def test_the_skip_choice_is_part_of_the_parse_cache_key(tmp_path):
    assert "more stretch" not in _text_of(_parse(tmp_path, include_hidden_sheets=False))
    assert "more stretch" in _text_of(_parse(tmp_path))  # not served the cached skipped variant


def test_spreadsheet_tables_are_compact(tmp_path):
    text = _text_of(_parse(tmp_path))
    rows = [ln for ln in text.split("\n") if ln.startswith("|")]
    assert "| Sr# | Sample | Customer | Standard | Test | Level | GSM | Lab |" in rows
    assert "| 1 | SDP 3101 | Northwind | EN 17092 | Tear | AA | 381 | Lab A |" in rows
    assert not any("  " in ln for ln in rows)  # no column padding


def _units(text: str):
    spans = segment_layout("c:0:h", text, [])
    units = asyncio.run(group_units([TaggedSpan(span=s) for s in spans]))
    check_units(spans, units)
    return units


_DB = "\n".join(["| Sr# | Sample | Customer | Standard | Test | Level |", "|---|---|---|---|---|---|"]
                + [f"| {i} | SDP {3100 + i} | Northwind | EN 17092 | Tear | AA |" for i in range(1, 6)])


def test_a_database_table_is_one_record_per_row():
    units = _units(_DB)
    assert len(units) == 5
    header = "| Sr# | Sample | Customer | Standard | Test | Level |"
    assert all(u.text.startswith(header) for u in units)  # each record carries its column names
    assert [u.text.split("\n")[-1] for u in units][-1] == "| 5 | SDP 3105 | Northwind | EN 17092 | Tear | AA |"


@pytest.mark.parametrize("table", [
    # a form grid: section title header, sparse value cells
    "| Washing | | | | | |\n|---|---|---|---|---|---|\n| Parameter | Proposed | Actual | Parameter | Proposed | Actual |\n"
    "| Washing Type | | | Temperature | | |\n| Time of Wash | | | Washing Speed | | |",
    # a key-value table: rows are fields of ONE record
    "| Manufacturer: | Northwind Mills |\n|---|---|\n| Style: | 107-208 |\n| Colour: | Navy |\n| Fibre: | 60/40 |",
    # a parameter grid of one fabric
    "| Parameter | Proposed | Actual |\n|---|---|---|\n| Gauge | 28G | 28G |\n| Stitch length | 2.85 mm | 2.9 mm |",
])
def test_forms_and_key_value_tables_stay_one_unit(table):
    assert len(_units(table)) == 1


def test_an_unnamed_serial_column_still_makes_a_record_table():
    """Spreadsheets often leave the index column's header blank."""
    table = "\n".join(["| | Criteria | Variable change | Finding |", "|---|---|---|---|"]
                      + [f"| {i} | Impact of gauge {i} | Gauge | pending |" for i in range(1, 4)])
    assert len(_units(table)) == 3


def test_merged_header_cells_do_not_hide_a_record_table():
    """A two-level header repeats a merged name across adjacent columns; the rows are still records."""
    table = "\n".join(
        ["| Sequence | Control Specimen | Control Specimen | Test Specimen | Test Specimen | Control Specimen |"
         " Control Specimen | C n | I n |", "|---|---|---|---|---|---|---|---|---|"]
        + [f"| {i} | C {i} | 1.{i} | T {i} | 6{i}.5 | C {i + 1} | 5.{i} | 1.{i} | 19.{i} |" for i in range(1, 6)])
    assert len(_units(table)) == 5


def test_a_repeated_section_title_header_is_still_a_form():
    table = "\n".join(["| Fabric Process Sheet | Fabric Process Sheet | Fabric Process Sheet |",
                       "|---|---|---|"] + [f"| {i} | Gauge | |" for i in range(1, 6)])
    assert len(_units(table)) == 1
