"""DD-1b (ADR-0117 / ADR-0067): the generic, backend-agnostic typed node/edge WRITE primitive `Store.kg_write`.
Hermetic — `object.__new__(ArcadeDBStore)` with `_db.execute_transaction` and `_property_types` captured; no DB.

`kg_write(nodes, edges)` upserts typed nodes (by `key_field`) then creates typed edges (FROM/TO by node key) in
ONE transaction. The domain store extensions (ContractKGStore/ComplianceStore) pass DOMAIN-NATIVE values; `kg_write`
owns ALL wire encoding, driven by each node type's PACK-DECLARED property storage type (so a pack never emits SQL,
and a new backend encodes natively). The decisive case: a `STRING`-typed field holding a list is stored as a JSON
string (today's `bbox`), while an `ARRAY_OF_INTEGERS` field is a native array (today's `pages`) -- only the declared
type disambiguates them.
"""
from __future__ import annotations

from rag_wright.store.arcadedb import ArcadeDBStore
from rag_wright.store.seam import KgEdge, KgNode


class _Recorder:
    def __init__(self) -> None:
        self.txns: list[list[str]] = []

    def execute_transaction(self, statements) -> None:
        self.txns.append(list(statements))


def _store(property_types: dict[str, dict[str, str]] | None = None):
    """A connection-less store: `_db` records transactions; `_property_types` returns the pack-declared types."""
    s = object.__new__(ArcadeDBStore)
    s._db = _Recorder()  # type: ignore[attr-defined]
    types = property_types or {}
    s._property_types = lambda type_name: types.get(type_name, {})  # type: ignore[attr-defined]
    return s


def test_kg_write_node_upsert_strings_in_prop_order():
    s = _store({"Clause": {"clause_id": "STRING", "function": "STRING"}})
    s.kg_write([KgNode("Clause", "clause_id", {"clause_id": "c1", "function": "cap_on_liability"})])
    assert s._db.txns == [
        ["UPDATE Clause SET clause_id = 'c1', function = 'cap_on_liability' UPSERT WHERE clause_id = 'c1'"]]


def test_kg_write_encodes_array_vs_json_string_by_declared_type():
    # the pages-vs-bbox case: ARRAY_OF_INTEGERS -> native array; STRING holding a list -> JSON string literal.
    s = _store({"Requirement": {"requirement_id": "STRING", "pages": "ARRAY_OF_INTEGERS", "bbox": "STRING"}})
    s.kg_write([KgNode("Requirement", "requirement_id",
                       {"requirement_id": "r1", "pages": [1, 2, 3], "bbox": [0.1, 0.2, 0.3, 0.4]})])
    stmt = s._db.txns[0][0]
    assert "pages = [1,2,3]" in stmt                       # native integer array
    assert "bbox = '[0.1, 0.2, 0.3, 0.4]'" in stmt         # json.dumps(...) as a quoted STRING literal


def test_kg_write_string_field_quotes_a_plain_string():
    s = _store({"Contract": {"contract_id": "STRING", "parties_json": "STRING"}})
    s.kg_write([KgNode("Contract", "contract_id", {"contract_id": "c1", "parties_json": ["Acme", "Beta"]})])
    assert "parties_json = '[\"Acme\", \"Beta\"]'" in s._db.txns[0][0]  # list under a STRING column -> JSON string


def test_kg_write_integer_and_null():
    s = _store({"Contract": {"contract_id": "STRING", "page_count": "INTEGER"}})
    s.kg_write([KgNode("Contract", "contract_id", {"contract_id": "c1", "page_count": None})])
    assert "page_count = null" in s._db.txns[0][0]
    s2 = _store({"Contract": {"contract_id": "STRING", "page_count": "INTEGER"}})
    s2.kg_write([KgNode("Contract", "contract_id", {"contract_id": "c1", "page_count": 7})])
    assert "page_count = 7" in s2._db.txns[0][0]


def test_kg_write_edge_create_with_type_driven_props_in_order():
    s = _store()
    s.kg_write([], [KgEdge("Caps", "Clause", "clause_id", "c1", "PropertyValue", "value_key", "cap_basis:fees",
                           {"dimension": "cap_basis", "confidence": "EXTRACTED"})])
    assert s._db.txns[0] == [
        "CREATE EDGE Caps FROM (SELECT FROM Clause WHERE clause_id = 'c1')"
        " TO (SELECT FROM PropertyValue WHERE value_key = 'cap_basis:fees')"
        " SET dimension = 'cap_basis', confidence = 'EXTRACTED'"]


def test_kg_write_edge_without_props_has_no_set_clause():
    s = _store()
    s.kg_write([], [KgEdge("Mentions", "Chunk", "chunk_id", "k1", "Entity", "entity_id", "e1", {})])
    assert s._db.txns[0] == [
        "CREATE EDGE Mentions FROM (SELECT FROM Chunk WHERE chunk_id = 'k1')"
        " TO (SELECT FROM Entity WHERE entity_id = 'e1')"]


def test_kg_write_one_transaction_nodes_before_edges():
    s = _store({"Clause": {"clause_id": "STRING"}, "PropertyValue": {"value_key": "STRING"}})
    s.kg_write(
        [KgNode("Clause", "clause_id", {"clause_id": "c1"}),
         KgNode("PropertyValue", "value_key", {"value_key": "d:v"})],
        [KgEdge("Caps", "Clause", "clause_id", "c1", "PropertyValue", "value_key", "d:v", {})])
    assert len(s._db.txns) == 1
    stmts = s._db.txns[0]
    assert stmts[0].startswith("UPDATE Clause") and stmts[1].startswith("UPDATE PropertyValue")
    assert stmts[2].startswith("CREATE EDGE Caps")


def test_kg_write_empty_is_a_noop():
    s = _store()
    s.kg_write([], [])
    assert s._db.txns == []  # nothing to write -> no transaction opened
