"""issue 0027 backfill: `add_affiliation_edges` is ADDITIVE and IDEMPOTENT -- create a node only if absent (never
clobber an existing entity), create an edge only if absent. Hermetic -- `_query`/`_command` stubbed, no live DB."""

from __future__ import annotations

from rag_wright.store.arcadedb import ArcadeDBStore
from rag_wright.store.seam import GraphEdge, GraphNode


def _bare_store(*, node_exists: set, edge_exists: bool):
    """A no-connection store whose `_query` answers node/edge existence and whose `_command` is captured."""
    commands: list = []
    s = object.__new__(ArcadeDBStore)

    def _query(sql: str):
        if sql.startswith("SELECT entity_id FROM Entity"):
            # a node existence probe: return a row iff one of the known-existing keys is in the SQL
            return [{"entity_id": "x"}] if any(f"'{k}'" in sql for k in node_exists) else []
        if "FROM Relationship" in sql:
            return [{"c": 1 if edge_exists else 0}]
        return []

    s._query = _query
    s._command = lambda sql: commands.append(sql)
    return s, commands


_NODES = [
    GraphNode(node_key="UNLINKED:acme holdings", entity_id="", name="Acme Holdings Ltd",
              entity_type="Organization", confidence="EXTRACTED", chunk_id="c0"),
    GraphNode(node_key="UNLINKED:acme", entity_id="", name="Acme Corp",
              entity_type="Organization", confidence="EXTRACTED", chunk_id="c0"),
]
_EDGES = [GraphEdge(source_key="UNLINKED:acme holdings", target_key="UNLINKED:acme",
                    relationship_type="Affiliate Of", confidence="EXTRACTED", chunk_id="c0")]


def test_creates_absent_node_and_edge_but_not_existing_ones():
    # 'acme' already a node (a party from another doc); 'acme holdings' absent; edge absent
    store, commands = _bare_store(node_exists={"UNLINKED:acme"}, edge_exists=False)
    added = store.add_affiliation_edges(_NODES, _EDGES)
    assert added == 1
    inserts = [c for c in commands if c.startswith("INSERT INTO Entity")]
    assert len(inserts) == 1 and "acme holdings" in inserts[0] and "Acme Corp" not in inserts[0]  # only the absent node
    assert any(c.startswith("CREATE EDGE Relationship") and "Affiliate Of" in c for c in commands)


def test_idempotent_when_node_and_edge_already_exist():
    # both nodes exist and the edge exists -> nothing created (a safe re-run)
    store, commands = _bare_store(node_exists={"UNLINKED:acme holdings", "UNLINKED:acme"}, edge_exists=True)
    added = store.add_affiliation_edges(_NODES, _EDGES)
    assert added == 0 and commands == []  # no INSERT, no CREATE EDGE
