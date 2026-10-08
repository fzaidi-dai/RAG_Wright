"""PS-6 (G21): the generic KG count / delete / update primitives and `key_range` on `kg_read`, so a pack's store
extension never emits store-native SQL. Hermetic SQL-contract tests (a connection-less `ArcadeDBStore` with captured
`_query` / `_command`) plus live ArcadeDB tests over the neutral engine schema."""
from __future__ import annotations

import pytest

from rag_wright.store.arcadedb import ArcadeDBStore
from rag_wright.store.seam import NOT_NULL, KgEdge, KgNode

_KINDS = [{"name": "Entity", "type": "vertex"}, {"name": "Relationship", "type": "edge"}]


def _store():
    """A connection-less store recording every SQL statement; `schema:types` reports Entity (vertex) and
    Relationship (edge); a count returns 7 and an update/delete reports 3 changed rows."""
    s = object.__new__(ArcadeDBStore)
    sql: list[str] = []

    def fake_query(q):
        sql.append(q)
        if "schema:types" in q:
            return list(_KINDS)
        return [{"n": 7}]

    def fake_command(q):
        sql.append(q)
        return [{"count": 3}]

    s._query, s._command = fake_query, fake_command  # type: ignore[attr-defined]
    s._ensured_packs = []  # type: ignore[attr-defined]
    return s, sql


# --- SQL contract -------------------------------------------------------------------------------------------------


def test_kg_read_key_range():
    s, sql = _store()
    s.kg_read("Entity", fields=["entity_id"], key_range=("entity_id", "C:", "C;"), order_by="entity_id")
    assert sql == ["SELECT entity_id FROM Entity WHERE entity_id >= 'C:' AND entity_id < 'C;' ORDER BY entity_id"]


def test_kg_count_with_where_and_key_range():
    s, sql = _store()
    assert s.kg_count("Entity", where={"entity_type": ["org", "person"], "name": NOT_NULL},
                      key_range=("entity_id", "a", "b")) == 7
    assert sql == ["SELECT count(*) AS n FROM Entity WHERE entity_type IN ['org','person'] AND name IS NOT NULL"
                   " AND entity_id >= 'a' AND entity_id < 'b'"]


def test_kg_count_of_a_whole_type():
    s, sql = _store()
    assert s.kg_count("Relationship") == 7 and sql == ["SELECT count(*) AS n FROM Relationship"]


def test_kg_delete_a_vertex_type_and_an_edge_type():
    s, sql = _store()
    assert s.kg_delete("Entity", where={"entity_id": "x"}) == 3
    assert s.kg_delete("Relationship") == 3
    deletes = [q for q in sql if q.startswith("DELETE")]
    assert deletes == ["DELETE FROM Entity WHERE entity_id = 'x'", "DELETE FROM Relationship UNSAFE"]


def test_kg_update_changes_only_rows_that_differ():
    s, sql = _store()
    assert s.kg_update("Relationship", set={"confidence": "AMBIGUOUS"}, where={"span_id": ["s1", "s2"]}) == 3
    assert sql == ["UPDATE Relationship SET confidence = 'AMBIGUOUS' WHERE span_id IN ['s1','s2']"
                   " AND (confidence IS NULL OR confidence <> 'AMBIGUOUS')"]


def test_kg_update_of_several_fields_changes_a_row_when_any_differs():
    s, sql = _store()
    s.kg_update("Entity", set={"name": "Acme", "rank": 2}, where={"entity_id": "e1"})
    assert sql == ["UPDATE Entity SET name = 'Acme', rank = 2 WHERE entity_id = 'e1'"
                   " AND (name IS NULL OR name <> 'Acme' OR rank IS NULL OR rank <> 2)"]


def test_kg_update_needs_values_to_set():
    s, _ = _store()
    with pytest.raises(ValueError):
        s.kg_update("Entity", set={}, where={"entity_id": "e1"})


def test_an_empty_membership_scopes_to_nothing_without_a_statement():
    s, sql = _store()
    assert s.kg_count("Entity", where={"entity_id": []}) == 0
    assert s.kg_delete("Entity", where={"entity_id": []}) == 0
    assert s.kg_update("Entity", set={"name": "x"}, where={"entity_id": []}) == 0
    assert s.kg_read("Entity", where={"entity_id": []}, key_range=("entity_id", "a", "b")) == []
    assert sql == []


def test_the_api_wrappers_delegate_to_the_workspace_store():
    from rag_wright.api import kg_count, kg_delete, kg_read, kg_update
    from rag_wright.api.workspace import WorkspaceHandle

    s, sql = _store()
    ws = WorkspaceHandle(s, None, "t")
    assert kg_count(ws, "Entity") == 7
    assert kg_update(ws, "Entity", set={"name": "x"}, where={"entity_id": "e"}) == 3
    assert kg_delete(ws, "Relationship", where={"confidence": "AMBIGUOUS"}) == 3
    kg_read(ws, "Entity", fields=["entity_id"], key_range=("entity_id", "a", "b"))
    assert sql[-1] == "SELECT entity_id FROM Entity WHERE entity_id >= 'a' AND entity_id < 'b'"


# --- live ArcadeDB (opt-in) ---------------------------------------------------------------------------------------


@pytest.fixture
def store():
    s = ArcadeDBStore.from_env(database="ragwright_test_kg_mutations", reset=True)
    s.ensure_schema()
    yield s
    s.drop()
    s.close()


def _seed(store):
    ents = [KgNode("Entity", "entity_id", {"entity_id": f"C:{i}", "name": f"E{i}", "entity_type": "org"})
            for i in range(3)] + [KgNode("Entity", "entity_id", {"entity_id": "D:0", "name": "D", "entity_type": "org"})]
    rels = [KgEdge("Relationship", "Entity", "entity_id", "C:0", "Entity", "entity_id", f"C:{i}",
                   {"span_id": f"s{i}", "confidence": "EXTRACTED"}) for i in (1, 2)]
    store.kg_write(ents, rels)


@pytest.mark.store
def test_live_count_update_delete_and_key_range(store):
    _seed(store)
    assert store.kg_count("Entity") == 4 and store.kg_count("Relationship") == 2
    assert store.kg_count("Entity", key_range=("entity_id", "C:", "C;")) == 3
    assert [r["entity_id"] for r in store.kg_read("Entity", fields=["entity_id"], key_range=("entity_id", "C:", "C;"),
                                                  order_by="entity_id")] == ["C:0", "C:1", "C:2"]

    assert store.kg_update("Relationship", set={"confidence": "AMBIGUOUS"}, where={"span_id": ["s1"]}) == 1
    assert store.kg_update("Relationship", set={"confidence": "AMBIGUOUS"}, where={"span_id": ["s1"]}) == 0  # idempotent
    assert store.kg_count("Relationship", where={"confidence": "AMBIGUOUS"}) == 1

    assert store.kg_delete("Relationship", where={"confidence": "AMBIGUOUS"}) == 1
    assert store.kg_count("Relationship") == 1
    assert store.kg_delete("Entity", key_range=("entity_id", "C:", "C;")) == 3  # takes the remaining edge with it
    assert store.kg_count("Entity") == 1 and store.kg_count("Relationship") == 0
