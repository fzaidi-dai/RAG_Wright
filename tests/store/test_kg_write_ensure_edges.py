"""PS-63 (TexWright, 2026-10-15; gap G8): `kg_write(..., ensure_edges=True)` writes an edge only once per (type, from
node, to node), within one call, across calls and across concurrent writers, with the last properties landing, all
in the one transaction. The ingestion default writer uses it, so two units emitting the same edge (an entity several
rows mention) leave one edge, and a re-ingest leaves one too. Without the flag `kg_write` is unchanged: a caller
whose parallel edges carry their own provenance keeps them."""
from __future__ import annotations

import asyncio
import concurrent.futures as cf

import pytest

from rag_wright.store.arcadedb import ArcadeDBStore
from rag_wright.store.seam import KgEdge, KgNode

_DB = "ragwright_test_ps63_ensure_edges"


class _Recorder:
    def __init__(self) -> None:
        self.txns: list[list[str]] = []

    def execute_transaction(self, statements) -> None:
        self.txns.append(list(statements))


def _store():
    s = object.__new__(ArcadeDBStore)
    s._db = _Recorder()  # type: ignore[attr-defined]
    s._property_types = lambda type_name: {}  # type: ignore[attr-defined]
    return s


def _edge(to="c1", **props):
    return KgEdge("HasChild", "Parent", "parent_key", "p1", "Child", "child_key", to, props)


def test_an_ensured_edge_is_created_only_if_absent_and_its_props_updated_first():
    s = _store()
    s.kg_write([], [_edge(w=1)], ensure_edges=True)
    (txn,) = s._db.txns
    assert txn == [
        "UPDATE (SELECT FROM (SELECT expand(outE('HasChild')) FROM Parent WHERE parent_key = 'p1')"
        " WHERE inV().child_key = 'c1') SET w = 1",
        "CREATE EDGE HasChild FROM (SELECT FROM Parent WHERE parent_key = 'p1')"
        " TO (SELECT FROM Child WHERE child_key = 'c1') IF NOT EXISTS SET w = 1"]


def test_the_same_edge_twice_in_one_call_is_written_once_with_the_last_props():
    s = _store()
    s.kg_write([], [_edge(w=1), _edge("c2"), _edge(w=2)], ensure_edges=True)
    (txn,) = s._db.txns
    creates = [x for x in txn if x.startswith("CREATE EDGE")]
    assert len(creates) == 2 and creates[0].endswith("SET w = 2") and "'c2'" in creates[1]


def test_an_ensured_edge_without_props_needs_no_update():
    s = _store()
    s.kg_write([], [_edge()], ensure_edges=True)
    assert s._db.txns == [["CREATE EDGE HasChild FROM (SELECT FROM Parent WHERE parent_key = 'p1')"
                           " TO (SELECT FROM Child WHERE child_key = 'c1') IF NOT EXISTS"]]


def test_without_the_flag_kg_write_still_creates_every_edge():
    s = _store()
    s.kg_write([], [_edge(w=1), _edge(w=2)])
    (txn,) = s._db.txns
    assert len(txn) == 2 and all(x.startswith("CREATE EDGE") and "IF NOT EXISTS" not in x for x in txn)


# --- live -----------------------------------------------------------------------------------------------------------

@pytest.fixture
def live():
    s = ArcadeDBStore.from_env(database=_DB, reset=True)
    for stmt in ("CREATE VERTEX TYPE Parent", "CREATE PROPERTY Parent.parent_key STRING",
                 "CREATE INDEX ON Parent (parent_key) UNIQUE", "CREATE VERTEX TYPE Child",
                 "CREATE PROPERTY Child.child_key STRING", "CREATE INDEX ON Child (child_key) UNIQUE",
                 "CREATE EDGE TYPE HasChild"):
        s._command(stmt)
    yield s
    s.close()


def _nodes():
    return [KgNode("Parent", "parent_key", {"parent_key": "p1"})] + [
        KgNode("Child", "child_key", {"child_key": f"c{i}"}) for i in (1, 2, 3)]


def _edges(s):
    return sorted((r["c"], r.get("w")) for r in s._query(
        "SELECT inV().child_key AS c, w FROM HasChild"))


@pytest.mark.store
def test_live_two_units_emitting_one_edge_leave_one_edge_and_a_rewrite_updates_it(live):
    # G8's reproduction: each child on two rows, both rows emit parent -> child; then the document is re-ingested
    live.kg_write(_nodes(), [_edge(f"c{i}", w=1) for i in (1, 2, 3) for _row in (0, 1)], ensure_edges=True)
    assert _edges(live) == [("c1", 1), ("c2", 1), ("c3", 1)]
    live.kg_write(_nodes(), [_edge(f"c{i}", w=2) for i in (1, 2, 3)], ensure_edges=True)
    assert _edges(live) == [("c1", 2), ("c2", 2), ("c3", 2)]
    live.kg_write([], [KgEdge("HasChild", "Child", "child_key", "c1", "Parent", "parent_key", "p1", {})],
                  ensure_edges=True)  # the reverse direction is a different edge
    assert live._query("SELECT count(*) AS n FROM HasChild")[0]["n"] == 4


@pytest.mark.store
def test_live_concurrent_writers_of_one_edge_leave_one_edge(live):
    live.kg_write(_nodes())
    with cf.ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda i: live.kg_write([], [_edge(f"c{i % 3 + 1}", w=1)], ensure_edges=True), range(48)))
    assert _edges(live) == [("c1", 1), ("c2", 1), ("c3", 1)]


@pytest.mark.store
def test_live_without_the_flag_parallel_edges_are_kept(live):
    live.kg_write(_nodes(), [_edge(w=1), _edge(w=2)])
    assert _edges(live) == [("c1", 1), ("c1", 2)]


@pytest.mark.store
def test_live_the_default_ingestion_writer_leaves_one_edge_per_shared_entity(live, tmp_path):
    from rag_wright.api import IngestSource, UnitExtraction, build_ingestion
    from tests.ingestion.test_builder import _FakeEmbedder
    from tests.ingestion.test_unreached_rows import _sheet
    from rag_wright.api.workspace import WorkspaceHandle
    from rag_wright.api import EngineConfig, StoreConfig

    live.ensure_schema()

    async def extract(unit, *, source_doc_id):  # every row names the one parent, so every unit emits the one edge
        return UnitExtraction(nodes=_nodes()[:2], edges=[_edge("c1", w=1, span_id=unit.anchor.span_id)])

    ws = WorkspaceHandle(live, EngineConfig(store=StoreConfig.from_env()), _DB)
    pipe = build_ingestion(extract, embedder=_FakeEmbedder(), progress=lambda _l: None)
    for _run in (1, 2):  # an ingest, then a re-ingest
        report = asyncio.run(pipe.aingest(ws, [IngestSource(path=str(_sheet(tmp_path, 6, 3)), doc_id="db",
                                                            table_mode="record")], cache_dir=tmp_path / "cache"))
        (doc,) = report.documents
        assert doc.units == 6 and not doc.extraction_failures and doc.dead_letter is None
    assert _edges(live) == [("c1", 1)]
    assert live._query("SELECT span_id FROM HasChild")[0]["span_id"].endswith("#6")  # the last unit's props landed
