"""Engine issue 0007 (0007-STORE): the SCALE-READY source filter on the Requirement KG.

A compliance check must be scopeable to named policy `source`s. The filter is pushed into the DATABASE
(`WHERE source IN [...]`) so a store holding thousands of requirement rows across many policies/tenants never
loads or embeds the ones outside the requested scope. `requirement_sources()` (DISTINCT source) powers
unknown-source validation without loading any rows.

Hermetic tests prove the SQL that gets built (the new logic); the `-m store` tests prove the real round-trip
against a live ArcadeDB (write across three sources, filter, distinct).
"""

from __future__ import annotations

import pytest

from rag_wright.contracts.compliance import DeonticType, Requirement
from rag_wright.store.arcadedb import ArcadeDBStore

_TEST_DB = "ragwright_test_req_sources"


def _req(source: str, i: int) -> Requirement:
    text = f"{source} rule {i}: a party must do the thing."
    return Requirement(
        requirement_id=Requirement.make_id(source, f"s{i}", text), source=source,
        citation=f"§ {i}", deontic_type=DeonticType.OBLIGATION, actor="party",
        applicability_scope=[], requirement_text=text)


# --- hermetic: the SQL the store builds (no live DB) --------------------------------------------


def _bare_store(capture: list, *, present_types=("Requirement",), rows=None):
    """An ArcadeDBStore with no connection: `_query` is captured, `type_names` stubbed."""
    s = object.__new__(ArcadeDBStore)
    s._query = lambda sql: (capture.append(sql) or (rows if rows is not None else []))
    s.type_names = lambda: set(present_types)
    return s


def test_all_requirements_without_sources_is_store_wide_no_where():
    cap: list = []
    _bare_store(cap).all_requirements()
    assert "FROM Requirement" in cap[0] and "WHERE" not in cap[0]  # unchanged store-wide behaviour


def test_all_requirements_filters_by_source_in_the_query_not_in_memory():
    cap: list = []
    _bare_store(cap).all_requirements(sources=["p1", "p2"])
    assert "WHERE source IN ['p1','p2']" in cap[0]  # DB-side filter -> other policies' rows never fetched


def test_all_requirements_empty_scope_returns_empty_without_a_query():
    cap: list = []
    out = _bare_store(cap).all_requirements(sources=[])
    assert out == [] and cap == []  # scope-to-nothing issues no query (and no invalid `IN []`)


def test_requirement_sources_is_a_distinct_query_dropping_nulls():
    cap: list = []
    s = _bare_store(cap, rows=[{"s": "p1"}, {"s": "p2"}, {"s": "p1"}, {"s": None}])
    assert s.requirement_sources() == {"p1", "p2"}
    assert "DISTINCT(source)" in cap[0] and "FROM Requirement" in cap[0]


def test_requirement_sources_on_a_fresh_db_is_empty_without_a_query():
    cap: list = []
    s = _bare_store(cap, present_types=())  # Requirement type not created yet
    assert s.requirement_sources() == set() and cap == []


# --- live ArcadeDB (opt-in): the real round-trip ------------------------------------------------


@pytest.fixture
def store():
    s = ArcadeDBStore.from_env(database=_TEST_DB, reset=True)
    s.ensure_compliance_schema()  # the Requirement KG lives in the compliance schema (a separate DB in prod)
    yield s
    s.close()


@pytest.mark.store
def test_source_filter_roundtrip_live(store):
    from rag_wright.capabilities.compliance_store import ComplianceStore

    ComplianceStore(store).write_requirements(  # DD-1b: writes moved to the compliance store extension
        [_req("p1", 0), _req("p1", 1), _req("p2", 0), _req("p2", 1), _req("p2", 2), _req("p3", 0)])

    assert {r["source"] for r in store.all_requirements()} == {"p1", "p2", "p3"}  # store-wide unchanged
    assert store.requirement_sources() == {"p1", "p2", "p3"}

    p1 = store.all_requirements(sources=["p1"])
    assert len(p1) == 2 and {r["source"] for r in p1} == {"p1"}  # ONLY the named policy
    assert {r["source"] for r in store.all_requirements(sources=["p1", "p3"])} == {"p1", "p3"}
    assert store.all_requirements(sources=[]) == []  # scope-to-nothing
    assert store.all_requirements(sources=["nope"]) == []  # a name with no rows -> no rows (validation is upstream)
