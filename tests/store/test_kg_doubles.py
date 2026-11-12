"""PS-64 (TexWright, 2026-11-09): ArcadeDB reads a bare decimal literal (`0.9523`) as a 32-bit FLOAT, so a DOUBLE
column never equalled the value the store had been given: `kg_update` rewrote an unchanged measurement on every call
(its `<>` test always true), equality and membership filters on a DOUBLE found nothing, and an undeclared float was
stored at 32 bits. A Python float is a double, so the store writes it as a double literal (`0.9523d`), except into
a column the pack declares FLOAT; a filter on an undeclared field also matches a value stored at 32 bits before."""
from __future__ import annotations

import pytest

from rag_wright.store.arcadedb import ArcadeDBStore, _kg_encode, _kg_sql, _kg_where_terms
from rag_wright.store.seam import KgEdge, KgNode, Range

_DB = "ragwright_test_kg_doubles"
_VALUES = (30.0, 1.5, 0.5, 0.9523, 21.77, 1.0268)  # TexWright's table: the first three settled, the last three never


def test_a_float_is_written_as_a_double_literal_unless_the_column_is_float():
    assert _kg_encode(0.9523, "DOUBLE") == "0.9523d"
    assert _kg_encode(0.9523, "DECIMAL") == "0.9523d"
    assert _kg_encode(0.9523, "FLOAT") == "0.9523"
    assert _kg_encode(3, "DOUBLE") == "3.0d"
    assert _kg_sql(21.77) == "21.77d" and _kg_sql([1.5, 2]) == "[1.5d,2]"  # undeclared and edge props: a double


def test_filters_encode_by_the_declared_type_and_an_undeclared_float_matches_either_width():
    types = {"x": "DOUBLE", "f": "FLOAT"}
    assert _kg_where_terms({"x": 0.9523}, types=types) == ["`x` = 0.9523d"]
    assert _kg_where_terms({"f": 0.9523}, types=types) == ["`f` = 0.9523"]
    assert _kg_where_terms({"u": 0.9523}, types=types) == ["(`u` = 0.9523d OR `u` = 0.9523)"]
    assert _kg_where_terms({"x": [0.9523, 2]}, types=types) == ["`x` IN [0.9523d,2]"]
    assert _kg_where_terms({"u": [0.9523]}, types=types) == ["(`u` IN [0.9523d] OR `u` IN [0.9523])"]
    assert _kg_where_terms({"x": Range(ge=1.5, lt=21.77)}, types=types) == ["`x` >= 1.5d", "`x` < 21.77d"]
    assert _kg_where_terms({"n": 3, "s": "a"}) == ["`n` = 3", "`s` = 'a'"]  # non-floats unchanged


# --- live -----------------------------------------------------------------------------------------------------------

def _pack(tmp_path):
    path = tmp_path / "doubles.ttl"
    path.write_text("@prefix eng: <https://ragwright.local/ontology/engine#> .\n"
                    "@prefix t: <https://example.test/t#> .\n"
                    't:R a eng:KgVertexType ; eng:vertexName "Report" ;\n'
                    '    eng:kgProperty "report_id:STRING", "reading:DOUBLE", "coarse:FLOAT" ;\n'
                    '    eng:uniqueIndexOn "report_id" .\n')
    return str(path)


@pytest.fixture
def live(tmp_path):
    s = ArcadeDBStore.from_env(database=_DB, reset=True)
    s.ensure_pack_schema(_pack(tmp_path))
    s._command("CREATE EDGE TYPE Cites")
    yield s
    s.close()


@pytest.mark.store
def test_live_setting_an_unchanged_double_changes_nothing(live):
    live.kg_write([KgNode("Report", "report_id", {"report_id": "r1"})])
    for v in _VALUES:
        assert live.kg_update("Report", set={"reading": v}, where={"report_id": "r1"}) == 1
        assert live.kg_update("Report", set={"reading": v}, where={"report_id": "r1"}) == 0, v
    (row,) = live.kg_read("Report", where={"report_id": "r1"})
    assert live.kg_update("Report", set={"reading": row["reading"]}, where={"report_id": "r1"}) == 0  # read back


@pytest.mark.store
def test_live_equality_membership_and_range_find_a_double(live):
    live.kg_write([KgNode("Report", "report_id", {"report_id": f"r{i}", "reading": v, "coarse": v})
                   for i, v in enumerate(_VALUES)])
    for v in _VALUES:
        assert live.kg_count("Report", where={"reading": v}) == 1, v
        assert live.kg_count("Report", where={"coarse": v}) == 1, v  # a declared FLOAT column still matches
    assert live.kg_count("Report", where={"reading": [0.9523, 21.77]}) == 2
    assert live.kg_count("Report", where={"reading": Range(ge=0.9523, le=0.9523)}) == 1


@pytest.mark.store
def test_live_an_undeclared_float_keeps_its_precision_and_old_32_bit_values_still_match(live):
    live.kg_write([KgNode("Report", "report_id", {"report_id": "r1", "extra": 123456789.12345679})],
                  [KgEdge("Cites", "Report", "report_id", "r1", "Report", "report_id", "r1", {"weight": 0.9523})])
    (row,) = live.kg_read("Report", where={"report_id": "r1"})
    assert row["extra"] == 123456789.12345679  # stored as a double, not cut to ~7 digits
    assert live.kg_count("Report", where={"extra": 123456789.12345679}) == 1
    assert live._query("SELECT weight FROM Cites")[0]["weight"] == 0.9523
    live._command("INSERT INTO Report SET report_id = 'old', extra = 21.77")  # how a float was stored before PS-64
    assert live.kg_count("Report", where={"extra": 21.77}) == 1
    assert live.kg_count("Report", where={"extra": [21.77]}) == 1
