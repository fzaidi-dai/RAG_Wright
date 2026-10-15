"""ER-1 (TexWright): range filters on any number of fields, evaluated in the store. A `Range` value in `where`
(`gt` / `ge` / `lt` / `le`, any combination of one lower and one upper bound) beside equality, membership and
`NOT_NULL`, on every KG call that takes `where`: `kg_read`, `kg_count`, `kg_delete`, `kg_update` and `kg_edges`.
`key_range` stays as the half-open shorthand. Hermetic SQL-contract tests plus a live ArcadeDB test."""
from __future__ import annotations

import pytest

from rag_wright.api import NOT_NULL, Range
from rag_wright.store.seam import KgNode
from tests.store.test_kg_mutations import _store


def test_a_range_on_each_of_several_fields():
    s, sql = _store()
    s.kg_read("Trial", fields=["trial_id"], where={"weight": Range(ge=180, le=220), "tensile": Range(gt=400.5),
                                                   "season": "ss26", "lot": Range(lt="L5")})
    assert sql == ["SELECT trial_id FROM Trial WHERE weight >= 180 AND weight <= 220 AND tensile > 400.5"
                   " AND season = 'ss26' AND lot < 'L5'"]


def test_kg_read_shares_the_filter_grammar_so_not_null_works_there_too():
    s, sql = _store()
    s.kg_read("Trial", where={"remark": NOT_NULL})
    assert sql == ["SELECT * FROM Trial WHERE remark IS NOT NULL"]


def test_count_delete_and_update_take_ranges():
    s, sql = _store()
    s.kg_count("Entity", where={"score": Range(gt=1)})
    s.kg_delete("Entity", where={"score": Range(le=0)})
    s.kg_update("Entity", set={"flag": "low"}, where={"score": Range(lt=2)})
    assert sql[0] == "SELECT count(*) AS n FROM Entity WHERE score > 1"
    assert "DELETE FROM Entity WHERE score <= 0" in sql
    assert any(q.startswith("UPDATE Entity SET") and "score < 2" in q for q in sql)


@pytest.mark.parametrize("kwargs", [{}, {"gt": 1, "ge": 1}, {"lt": 1, "le": 1}, {"gt": [1]}, {"le": None}])
def test_a_range_needs_one_bound_per_side_and_scalar_bounds(kwargs):
    with pytest.raises((ValueError, TypeError)):
        Range(**kwargs)


@pytest.fixture
def store():
    from rag_wright.store.arcadedb import ArcadeDBStore

    s = ArcadeDBStore.from_env(database="ragwright_test_kg_range", reset=True)
    s.ensure_schema()
    yield s
    s.drop()
    s.close()


@pytest.mark.store
def test_live_ranges_on_two_numeric_fields_together(store):
    rows = [("t1", 170, 380.0), ("t2", 185, 410.0), ("t3", 200, 450.5), ("t4", 230, 500.0), ("t5", 210, 390.0)]
    store.kg_write([KgNode("Entity", "entity_id", {"entity_id": i, "name": i, "entity_type": "trial",
                                                     "weight": w, "tensile": t}) for i, w, t in rows])
    where = {"weight": Range(ge=180, le=220), "tensile": Range(gt=400)}
    assert [r["entity_id"] for r in store.kg_read("Entity", fields=["entity_id"], where=where,
                                                  order_by="entity_id")] == ["t2", "t3"]
    assert store.kg_count("Entity", where=where) == 2
    assert store.kg_count("Entity", where={"weight": Range(lt=180)}) == 1
