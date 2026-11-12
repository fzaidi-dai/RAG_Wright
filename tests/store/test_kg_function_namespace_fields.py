"""PS-68 (TexWright, 2026-11-12): ArcadeDB 26.11.1 reads `text.toLowerCase()` as the function `toLowerCase` of a
module named `text`, so a `Contains` filter on a field named `text` (or `date`, `math`, `util`: any function
namespace) was refused, in both case modes, while 26.7.1 read it as a method on the field. Every field the filter
grammar writes is now quoted (`` `text` ``), which settles any field name a future function namespace could take;
record attributes (`@rid`, ...) stay as they are."""
from __future__ import annotations

import pytest

from rag_wright.store.arcadedb import ArcadeDBStore, _kg_where_terms
from rag_wright.store.seam import NOT_NULL, Contains, KgEdge, KgNode, Range

_DB = "ragwright_test_kg_function_namespace_fields"
_NAMES = ("text", "date", "math", "util")


def test_every_filter_field_is_quoted_and_a_record_attribute_is_not():
    assert _kg_where_terms({"text": Contains("kev")}) == ["`text`.toLowerCase().indexOf('kev'.toLowerCase()) >= 0"]
    assert _kg_where_terms({"text": Contains("Kev", case_sensitive=True)}) == ["`text`.indexOf('Kev') >= 0"]
    assert _kg_where_terms({"a": "x", "b": [1, 2], "c": NOT_NULL, "d": Range(ge=1)}) == [
        "`a` = 'x'", "`b` IN [1,2]", "`c` IS NOT NULL", "`d` >= 1"]
    assert _kg_where_terms({"u": 0.5}) == ["(`u` = 0.5d OR `u` = 0.5)"]
    assert _kg_where_terms({"u": [0.5]}) == ["(`u` IN [0.5d] OR `u` IN [0.5])"]
    assert _kg_where_terms(None, ("k", "a", "b")) == ["`k` >= 'a'", "`k` < 'b'"]
    assert _kg_where_terms({"@rid": "#1:0"}) == ["@rid = '#1:0'"]


# --- live -----------------------------------------------------------------------------------------------------------

@pytest.fixture
def live():
    s = ArcadeDBStore.from_env(database=_DB, reset=True)
    for stmt in ("CREATE VERTEX TYPE Rec", "CREATE PROPERTY Rec.k STRING", "CREATE INDEX ON Rec (k) UNIQUE",
                 "CREATE EDGE TYPE Has"):
        s._command(stmt)
    s.kg_write([KgNode("Rec", "k", {"k": "a", **{n: f"Kevlar {n}" for n in _NAMES}}),
                KgNode("Rec", "k", {"k": "b", **{n: f"cotton {n}" for n in _NAMES}})],
               [KgEdge("Has", "Rec", "k", "a", "Rec", "k", "b", {n: f"edge {n}" for n in _NAMES})])
    yield s
    s.drop()
    s.close()


@pytest.mark.store
@pytest.mark.parametrize("name", _NAMES)
def test_live_every_filter_on_a_function_namespace_field(live, name):
    keys = lambda where: sorted(r["k"] for r in live.kg_read("Rec", where=where, fields=["k"]))  # noqa: E731
    assert keys({name: Contains("kevlar")}) == ["a"]
    assert keys({name: Contains("Kevlar", case_sensitive=True)}) == ["a"]
    assert keys({name: f"cotton {name}"}) == ["b"]
    assert keys({name: [f"cotton {name}", "x"]}) == ["b"]
    assert keys({name: NOT_NULL}) == ["a", "b"]
    assert keys({name: Range(ge="L")}) == ["b"]
    assert live.kg_count("Rec", where={name: Contains("COTTON")}) == 1
    hits = live.kg_edges("Rec", edge_type="Has", where={name: Contains("kev")}, edge_where={name: Contains("edge")},
                         target_where={name: Contains("cot")}, select={"src": "c.k", "dst": "v.k"})
    assert [(h["src"], h["dst"]) for h in hits] == [("a", "b")]
    assert live.kg_update("Rec", set={"k2": "x"}, where={name: Contains("cotton")}) == 1
    assert live.kg_delete("Rec", where={name: Contains("cotton")}) == 1
