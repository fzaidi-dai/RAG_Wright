"""DD-1a (ADR-0117 / ADR-0067): the generic, backend-agnostic typed-node read primitive `Store.kg_read`, and the
parity of the first three domain reads re-expressed onto it. Hermetic — `object.__new__(ArcadeDBStore)` with a
captured `_query` (the established store-test pattern); no ArcadeDB connection.

`kg_read(node_type, *, where, fields, distinct, order_by, limit)` is the generic read the domain store extensions
(DD-1b) will delegate to, so a domain pack never emits ArcadeDB SQL. These tests pin its SQL contract, then prove
`all_requirements` / `contract_by_id` / `spans_by_contract` emit the IDENTICAL SQL through it (no behavior change).
"""
from __future__ import annotations

from rag_wright.store.arcadedb import ArcadeDBStore


def _store_capturing_sql():
    """A connection-less store whose `_query` records the SQL it is handed and returns canned rows."""
    s = object.__new__(ArcadeDBStore)
    captured: dict[str, str] = {}
    calls: list[str] = []
    rows_to_return: list[dict] = []

    def fake_query(sql: str):
        captured["sql"] = sql
        calls.append(sql)
        return list(rows_to_return)

    s._query = fake_query  # type: ignore[attr-defined]
    return s, captured, calls, (lambda r: rows_to_return.__iadd__(r))


# --- kg_read SQL contract ---

def test_kg_read_equality_filter_and_fields():
    s, cap, _, _ = _store_capturing_sql()
    s.kg_read("T", fields=["a", "b"], where={"x": "v"})
    assert cap["sql"] == "SELECT a, b FROM T WHERE x = 'v'"


def test_kg_read_no_fields_selects_star():
    s, cap, _, _ = _store_capturing_sql()
    s.kg_read("T")
    assert cap["sql"] == "SELECT * FROM T"


def test_kg_read_in_filter_for_list_value():
    s, cap, _, _ = _store_capturing_sql()
    s.kg_read("T", fields=["a"], where={"s": ["x", "y"]})
    assert cap["sql"] == "SELECT a FROM T WHERE s IN ['x','y']"


def test_kg_read_empty_list_value_returns_empty_without_querying():
    s, _, calls, _ = _store_capturing_sql()
    out = s.kg_read("T", where={"s": []})
    assert out == [] and calls == []  # scope-to-nothing: never issue an invalid `IN []`


def test_kg_read_multi_filter_is_anded_in_insertion_order_with_order_by():
    s, cap, _, _ = _store_capturing_sql()
    s.kg_read("Span", fields=["span_id"], where={"contract_id": "c1", "function": ["cap"]}, order_by="doc_start")
    assert cap["sql"] == "SELECT span_id FROM Span WHERE contract_id = 'c1' AND function IN ['cap'] ORDER BY doc_start"


def test_kg_read_distinct_and_limit():
    s, cap, _, _ = _store_capturing_sql()
    s.kg_read("Requirement", distinct="source", limit=5)
    assert cap["sql"] == "SELECT DISTINCT(source) AS source FROM Requirement LIMIT 5"


def test_kg_read_returns_rows_unshaped():
    s, _, _, add = _store_capturing_sql()
    add([{"a": 1}, {"a": 2}])
    assert s.kg_read("T", fields=["a"]) == [{"a": 1}, {"a": 2}]


# --- parity: the three re-expressed reads emit the identical SQL (no behavior change) ---

_REQ_SQL = ("SELECT requirement_id, source, citation, deontic_type, actor, requirement_text, evidence_standard,"
            " severity, applicability_json, confidence, pages, bbox FROM Requirement")


def test_all_requirements_parity():
    s, cap, calls, _ = _store_capturing_sql()
    s.all_requirements()
    assert cap["sql"] == _REQ_SQL
    s.all_requirements(sources=["FTC-16CFR255"])
    assert cap["sql"] == _REQ_SQL + " WHERE source IN ['FTC-16CFR255']"
    calls.clear()
    assert s.all_requirements(sources=[]) == [] and calls == []  # empty scope -> [] without a query


def test_contract_by_id_parity():
    s, cap, _, add = _store_capturing_sql()
    assert s.contract_by_id("c1") is None  # no rows -> None
    assert cap["sql"] == (
        "SELECT contract_id, name, agreement_type, parties_json, agreement_date, effective_date,"
        " source_doc_id, content_hash, page_count FROM Contract WHERE contract_id = 'c1'")
    add([{"contract_id": "c1"}])
    assert s.contract_by_id("c1") == {"contract_id": "c1"}  # row -> the row


def test_spans_by_contract_parity():
    s, cap, calls, _ = _store_capturing_sql()
    assert s.spans_by_contract("c1", []) == [] and calls == []  # empty functions -> [] without a query
    s.spans_by_contract("c1", ["cap_on_liability"])
    assert cap["sql"] == (
        "SELECT span_id, parent_chunk_id, parent_okf_path, span_index, text, function, contract_id, doc_start,"
        " doc_end, pages, bbox FROM Span WHERE contract_id = 'c1' AND function IN ['cap_on_liability']"
        " ORDER BY doc_start")
