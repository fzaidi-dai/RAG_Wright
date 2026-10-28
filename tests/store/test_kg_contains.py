"""PS-54 (TexWright, 2026-10-19 request): a `Contains` where value, "this string field contains this text", evaluated in
the store beside equality, membership, `NOT_NULL` and `Range`, on every KG call that takes `where`. The match is literal
(no wildcards: `%`, `_` and `?` are ordinary characters), case-insensitive unless asked, and a field with no value
never matches. Hermetic SQL-contract tests plus a live ArcadeDB test."""
from __future__ import annotations

import pytest

from rag_wright.api import Contains, Range
from rag_wright.store.seam import KgEdge, KgNode
from tests.store.test_kg_mutations import _store


def test_contains_is_a_case_insensitive_literal_match_by_default():
    s, sql = _store()
    s.kg_read("Trial", fields=["trial_id"], where={"composition": Contains("UHMWPE")})
    assert sql == ["SELECT trial_id FROM Trial WHERE composition.toLowerCase().indexOf('UHMWPE'.toLowerCase()) >= 0"]


def test_case_sensitive_contains():
    s, sql = _store()
    s.kg_read("Trial", where={"composition": Contains("Kevlar", case_sensitive=True)})
    assert sql == ["SELECT * FROM Trial WHERE composition.indexOf('Kevlar') >= 0"]


def test_contains_composes_with_the_rest_of_the_grammar_and_escapes_its_text():
    s, sql = _store()
    s.kg_read("Trial", where={"composition": Contains("it's 50%"), "gsm": Range(ge=250, le=320), "season": "ss26"})
    assert sql == ["SELECT * FROM Trial WHERE composition.toLowerCase().indexOf('it\\'s 50%'.toLowerCase()) >= 0"
                   " AND gsm >= 250 AND gsm <= 320 AND season = 'ss26'"]


def test_count_delete_update_and_edges_take_contains():
    s, sql = _store()
    s.kg_count("Entity", where={"name": Contains("acme")})
    s.kg_delete("Entity", where={"name": Contains("tmp")})
    s.kg_update("Entity", set={"flag": "x"}, where={"name": Contains("old")})
    s.kg_edges(edge_type="Relationship", select={"src": "outV().entity_id"},
               edge_where={"relationship_type": Contains("supplies")})
    assert sql[0] == "SELECT count(*) AS n FROM Entity WHERE name.toLowerCase().indexOf('acme'.toLowerCase()) >= 0"
    assert "DELETE FROM Entity WHERE name.toLowerCase().indexOf('tmp'.toLowerCase()) >= 0" in sql
    assert any(q.startswith("UPDATE Entity SET") and "name.toLowerCase().indexOf('old'.toLowerCase()) >= 0" in q
               for q in sql)
    assert ("SELECT outV().entity_id AS src FROM Relationship"
            " WHERE relationship_type.toLowerCase().indexOf('supplies'.toLowerCase()) >= 0") in sql


@pytest.mark.parametrize("args", [("",), (5,), (None,), (["a"],)])
def test_contains_needs_non_empty_text(args):
    with pytest.raises((ValueError, TypeError)):
        Contains(*args)


@pytest.fixture
def store():
    from rag_wright.store.arcadedb import ArcadeDBStore

    s = ArcadeDBStore.from_env(database="ragwright_test_kg_contains", reset=True)
    s.ensure_schema()
    yield s
    s.drop()
    s.close()


@pytest.mark.store
def test_live_contains(store):
    rows = {"t1": ("100% Para-Aramid", 300), "t2": ("CSP-004 White + 250-D UHMWPE White", 260),
            "t3": ("Kevlar blend", 280), "t4": ("kevlar 50%_x", 400), "t5": (None, 290),
            "t6": ("it's 50% back\\slash?", 250), "t7": ("ÉTOILE weave", 270)}
    nodes = []
    for i, (comp, gsm) in rows.items():
        props = {"entity_id": i, "name": i, "entity_type": "trial", "gsm": gsm}
        if comp is not None:
            props["composition"] = comp
        nodes.append(KgNode("Entity", "entity_id", props))
    store.kg_write(nodes)

    def ids(where):
        return [r["entity_id"] for r in store.kg_read("Entity", fields=["entity_id"], where=where,
                                                      order_by="entity_id")]

    assert ids({"composition": Contains("uhmwpe")}) == ["t2"]
    assert ids({"composition": Contains("KEVLAR")}) == ["t3", "t4"]                        # case folds both ways
    assert ids({"composition": Contains("Kevlar", case_sensitive=True)}) == ["t3"]
    assert ids({"composition": Contains("étoile")}) == ["t7"]
    assert ids({"composition": Contains("50%_")}) == ["t4"]                                # no wildcards
    assert ids({"composition": Contains("?")}) == ["t6"]
    assert ids({"composition": Contains("it's 50% back\\slash")}) == ["t6"]                # escaped literal
    assert ids({"composition": Contains(" ")}) == ["t1", "t2", "t3", "t4", "t6", "t7"]  # t5 has no value
    assert ids({"composition": Contains("kevlar"), "gsm": Range(le=300)}) == ["t3"]
    assert store.kg_count("Entity", where={"composition": Contains("kevlar")}) == 2

    store.kg_write([], [KgEdge("Relationship", "Entity", "entity_id", "t3", "Entity", "entity_id", "t2",
                               {"relationship_type": "Supplies yarn", "source_doc_id": "d"})])
    hits = store.kg_edges(from_type="Entity", edge_type="Relationship", where={"composition": Contains("kevlar")},
                          edge_where={"relationship_type": Contains("SUPPLIES")},
                          target_where={"composition": Contains("uhmwpe")}, select={"dst": "v.entity_id"})
    assert [h["dst"] for h in hits] == ["t2"]
