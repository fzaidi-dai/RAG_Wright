"""PS-53 (TexWright, 2026-10-21): the KG calls escaped filter VALUES but interpolated type and field NAMES raw, so a
caller-chosen field was SQL injection (`where={"1=1 OR id": NOT_NULL}` returned every row). Every identifier a KG
call puts into SQL is now checked against the identifier grammar before any statement is built: a name that is not
one raises `ValueError`, and nothing is sent. Hermetic: a connection-less store recording every statement."""
from __future__ import annotations

import pytest

from rag_wright.store.arcadedb import ArcadeDBStore
from rag_wright.store.seam import NOT_NULL, KgEdge, KgNode, Range

_BAD = "1=1 OR x"


def _store():
    s = object.__new__(ArcadeDBStore)
    sent: list[str] = []

    def record(q):
        sent.append(q)
        if "schema:types" in q:
            return [{"name": "T", "type": "vertex"}, {"name": "E", "type": "edge"}]
        return [{"n": 0, "count": 0}]

    s._query, s._command = record, record  # type: ignore[attr-defined]
    s._ensured_packs = []  # type: ignore[attr-defined]
    s._db = type("Db", (), {"execute_transaction": staticmethod(lambda stmts: sent.extend(stmts))})()
    return s, sent


_REJECTED = {
    "kg_read where": lambda s: s.kg_read("T", where={_BAD: NOT_NULL}),
    "kg_read where Range": lambda s: s.kg_read("T", where={_BAD: Range(ge=1)}),
    "kg_read where list": lambda s: s.kg_read("T", where={_BAD: [1]}),
    "kg_read key_range": lambda s: s.kg_read("T", key_range=(_BAD, 0, 1)),
    "kg_read fields": lambda s: s.kg_read("T", fields=["a", _BAD]),
    "kg_read distinct": lambda s: s.kg_read("T", distinct=_BAD),
    "kg_read order_by": lambda s: s.kg_read("T", order_by="a; DELETE FROM T"),
    "kg_read type": lambda s: s.kg_read("T WHERE 1=1 --"),
    "kg_count where": lambda s: s.kg_count("T", where={_BAD: 1}),
    "kg_count type": lambda s: s.kg_count("T, E"),
    "kg_delete where": lambda s: s.kg_delete("T", where={_BAD: 1}),
    "kg_delete type": lambda s: s.kg_delete("T WHERE 1=1"),
    "kg_update set": lambda s: s.kg_update("T", set={"a = 1, b": 2}, where={"k": 1}),
    "kg_update where": lambda s: s.kg_update("T", set={"a": 1}, where={_BAD: 1}),
    "kg_write node prop": lambda s: s.kg_write([KgNode("T", "k", {"k": "1", "a = 1, b": 2})]),
    "kg_write node type": lambda s: s.kg_write([KgNode("T SET x = 1", "k", {"k": "1"})]),
    "kg_write key_field": lambda s: s.kg_write([KgNode("T", "k = 1 OR k", {"k = 1 OR k": "1"})]),
    "kg_write edge type": lambda s: s.kg_write([], [KgEdge("E SET x = 1", "T", "k", "1", "T", "k", "2", {})]),
    "kg_write edge from_key_field": lambda s: s.kg_write([], [KgEdge("E", "T", _BAD, "1", "T", "k", "2", {})]),
    "kg_write edge prop": lambda s: s.kg_write([], [KgEdge("E", "T", "k", "1", "T", "k", "2", {"a = 1, b": 2})]),
    "kg_ensure_edges to_type": lambda s: s.kg_ensure_edges([KgEdge("E", "T", "k", "1", "T)", "k", "2", {})]),
    "kg_edges from_type": lambda s: s.kg_edges("T, as: x}", where={"k": 1}, select={"a": "v.a"}),
    "kg_edges edge_type": lambda s: s.kg_edges("T", where={"k": 1}, edge_type="E')", select={"a": "v.a"}),
    "kg_edges where": lambda s: s.kg_edges("T", where={_BAD: 1}, select={"a": "v.a"}),
    "kg_edges edge_where": lambda s: s.kg_edges("T", where={"k": 1}, edge_where={_BAD: 1}, select={"a": "v.a"}),
    "kg_edges target_where": lambda s: s.kg_edges("T", where={"k": 1}, target_where={_BAD: 1}, select={"a": "v.a"}),
    "kg_edges select alias": lambda s: s.kg_edges("T", where={"k": 1}, select={"a, v.secret AS b": "v.a"}),
    "kg_edges select expr": lambda s: s.kg_edges("T", where={"k": 1}, select={"a": "(SELECT FROM T)"}),
}


@pytest.mark.parametrize("call", list(_REJECTED.values()), ids=list(_REJECTED))
def test_a_name_that_is_not_an_identifier_is_refused_before_any_sql(call):
    s, sent = _store()
    with pytest.raises(ValueError, match="identifier"):
        call(s)
    assert not [q for q in sent if "schema:types" not in q]


def test_the_identifiers_real_callers_use_are_accepted():
    s, sent = _store()
    s.kg_read("T", where={"a_1": NOT_NULL, "@rid": "#1:0"}, fields=["a_1", "@type"], order_by="a_1")
    s.kg_read("T", order_by="a DESC, b asc")
    s.kg_read("T", distinct="a_1")
    s.kg_edges("T", where={"k": 1}, edge_type="E", select={
        "v": "v.value", "c": "c.clause_id", "t": "e.@type", "i": "inV().trial_id", "o": "outV().sdf_number", "d": "span_id"})
    s.kg_edges(edge_type="E", edge_where={"confidence": "EXTRACTED"}, select={"s": "outV().k"})
    s.kg_write([KgNode("T", "k", {"k": "1", "a": 2})], [KgEdge("E", "T", "k", "1", "T", "k", "2", {"span_id": "s"})])
    assert len([q for q in sent if "schema:types" not in q]) == 5 + 2  # five reads + the write's two statements
