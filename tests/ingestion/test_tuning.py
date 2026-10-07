"""ING-4b (ADR-0124): `IngestionTuning` and the per-source table mode drive the default hooks."""
from __future__ import annotations

import asyncio

from rag_wright.api import IdentifierRule, IngestionTuning, RecordTableRule, TaggedSpan, check_units
from rag_wright.ingestion import group_units, segment_layout


def _units(text: str, **kw):
    spans = segment_layout("c:0:h", text, [], tuning=kw.pop("seg_tuning", None))
    units = asyncio.run(group_units([TaggedSpan(span=s) for s in spans], **kw))
    check_units(spans, units)
    return units


_SIX_COLS = "\n".join(["| Sample | Customer | Standard | Test | Level | Lab |", "|---|---|---|---|---|---|"]
                      + [f"| SDP {i} | Northwind | EN 388 | Cut | {i} | Lab A |" for i in range(1, 5)])
_FORM = "\n".join(["| Washing | | |", "|---|---|---|", "| Temperature | 60 | |", "| Time | 30 | |"])


def test_default_rule_keeps_a_six_column_non_serial_table_whole():
    assert len(_units(_SIX_COLS)) == 1


def test_a_tuned_record_rule_splits_it_per_row():
    tuning = IngestionTuning(record_table=RecordTableRule(min_cols=6))
    assert len(_units(_SIX_COLS, tuning=tuning)) == 4


def test_table_mode_record_forces_rows_and_block_forces_one_unit():
    assert len(_units(_FORM, table_mode="record")) == 2
    assert len(_units(_SIX_COLS, tuning=IngestionTuning(record_table=RecordTableRule(min_cols=6)),
                      table_mode="block")) == 1


def test_the_unit_cap_comes_from_tuning():
    text = " ".join(f"Sentence number {i} of a long section." for i in range(12))
    assert len(_units(text, tuning=IngestionTuning(max_unit_chars=120))) > 1
    assert len(_units(text)) == 1


def test_the_fragment_floor_comes_from_tuning():
    text = "AB\n\nYarn count: 40s Ne."
    assert len(segment_layout("c:0:h", text, [])) == 2
    assert len(segment_layout("c:0:h", text, [], tuning=IngestionTuning(min_fragment_alnum=3))) == 1


def test_the_identifier_rule_comes_from_tuning(tmp_path):
    from rag_wright.api import parse_document

    from tests.corpus._ooxml_fixtures import fake_pdf, make_xlsx, packager

    rows = [["Sr#", "Sample", "Test"], [1, "SDP 3101", "Tear"], [1, "SDP 3101", "Abrasion"]]
    x = make_xlsx(tmp_path / "db.xlsx", rows, [(2, 1, "oleObject1.bin", packager("SDP 3101.pdf", fake_pdf("1")))])
    strict = IngestionTuning(identifier=IdentifierRule(max_rows=1))  # '3101' is on 2 rows -> not an identifier
    (child,) = parse_document("db", x, cache_dir=tmp_path / "c1", tuning=strict).embedded
    assert [(lk.confidence.value, lk.basis) for lk in child.links] == [("INFERRED", "anchor")]
    (child,) = parse_document("db", x, cache_dir=tmp_path / "c2").embedded
    assert ("EXTRACTED", "anchor") in [(lk.confidence.value, lk.basis) for lk in child.links]
