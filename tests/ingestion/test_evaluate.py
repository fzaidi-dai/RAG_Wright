"""ING-4b (ADR-0124): `evaluate_ingestion` -- the structural eval a developer runs on their own samples to set
`IngestionTuning` (parse + chunk + segment + group only: no model, no store)."""
from __future__ import annotations

from pathlib import Path

from rag_wright.api import IngestionTuning, IngestSource, evaluate_ingestion

from tests.corpus._ooxml_fixtures import make_xlsx

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ingestion"


def test_clean_samples_pass_every_structural_check(tmp_path):
    ev = evaluate_ingestion([str(FIXTURES / "textile_spec_sheet.md"), str(FIXTURES / "textile_test_report.md")],
                            cache_dir=tmp_path)
    assert ev.passed, ev.failures
    (spec, _report) = ev.documents
    assert spec.tiles and spec.units == 5 and spec.tables == 1 and spec.tables_whole == 1.0
    assert spec.headings_start_units == 1.0 and spec.coverage == 1.0 and spec.bare_heading_spans == 0


def test_table_labels_catch_a_form_split_into_rows(tmp_path):
    rows = [["Parameter", "Proposed", "Actual"], ["Gauge", "28G", "28G"], ["Stitch", "2.8", "2.9"]]
    x = make_xlsx(tmp_path / "grid.xlsx", rows, [])
    labels = [{"pattern": r"^\| Parameter \| Proposed", "label": "block"}]
    ok = evaluate_ingestion([str(x)], cache_dir=tmp_path / "a", table_labels=labels)
    bad = evaluate_ingestion([IngestSource(path=str(x), table_mode="record")], cache_dir=tmp_path / "b",
                             table_labels=labels)
    assert ok.passed and not bad.passed
    assert any("block table split" in f for f in bad.failures)


def test_unlabelled_tables_are_listed_when_labels_are_given(tmp_path):
    rows = [["Sr#", "Sample", "Test"], [1, "SDP 1", "Tear"], [2, "SDP 2", "Cut"]]
    x = make_xlsx(tmp_path / "db.xlsx", rows, [])
    ev = evaluate_ingestion([str(x)], cache_dir=tmp_path, table_labels=[])
    assert ev.unlabelled_tables == ["| Sr# | Sample | Test |"] and not ev.passed


def test_tuning_is_applied(tmp_path):
    loose = evaluate_ingestion([str(FIXTURES / "textile_spec_sheet.md")], cache_dir=tmp_path / "a")
    tight = evaluate_ingestion([str(FIXTURES / "textile_spec_sheet.md")], cache_dir=tmp_path / "b",
                               tuning=IngestionTuning(max_unit_chars=100))
    assert tight.documents[0].units > loose.documents[0].units
