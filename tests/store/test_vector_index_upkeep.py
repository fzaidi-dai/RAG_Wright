"""PS-67 (TexWright, 2026-11-12): the first nearest-neighbour query in a fresh store process loads, or builds, the
dense index's vector graph, so its cost lands on a user's first search. `warm_vector_indexes` pays it deliberately:
one neighbour query per engine dense index that holds a vector, each timed, for a deployment's start-up before it
serves. An index with no vectors is skipped (there is no graph to load).

`rebuild_vector_indexes` is the one-time step after a database written by ArcadeDB 26.7 is first opened by 26.11:
that open rebuilds the graph by itself, and on real data it can leave nearly every vector reachable only by a full
scan (139,938 of 139,955 on `ragwright_cuad_full`); an explicit `REBUILD INDEX` builds it properly (1,765)."""
from __future__ import annotations

import random

import pytest

from rag_wright.api import EngineConfig, StoreConfig, rebuild_vector_indexes, warm_vector_indexes
from rag_wright.api.workspace import WorkspaceHandle
from rag_wright.contracts.chunk import BGE_M3_DENSE_DIM
from rag_wright.store.arcadedb import ArcadeDBStore

_DB = "ragwright_test_vector_index_upkeep"


class _Recorder:
    def __init__(self) -> None:
        self.sql: list[str] = []

    def query(self, sql: str) -> list[dict]:
        self.sql.append(sql)
        return [{"dense": [0.5, 0.5]}] if sql.startswith("SELECT dense FROM Span") else []


def _store():
    s = object.__new__(ArcadeDBStore)
    rec = _Recorder()
    s._query = rec.query  # type: ignore[method-assign]
    s._command = rec.sql.append  # type: ignore[method-assign]
    s.index_names = lambda: {"Chunk[dense]", "Span[dense]", "Span[span_id]"}  # type: ignore[method-assign]
    return s, rec


def test_each_dense_index_holding_a_vector_gets_one_neighbour_query():
    s, rec = _store()
    took = s.warm_vector_indexes()
    assert list(took) == ["Span[dense]"] and took["Span[dense]"] >= 0  # Chunk holds no vector: skipped
    neighbours = [q for q in rec.sql if "vector.neighbors" in q]
    assert neighbours == ["SELECT expand(`vector.neighbors`('Span[dense]', [0.5,0.5], 1))"]


def test_an_absent_index_is_not_touched():
    s, rec = _store()
    s.index_names = lambda: set()  # type: ignore[method-assign]
    assert s.warm_vector_indexes() == {} and rec.sql == []


def test_the_api_warms_the_workspace_store():
    s, _ = _store()
    ws = WorkspaceHandle(s, EngineConfig(store=StoreConfig(host="x", port="0", user="u", password="p")), "w")
    assert list(warm_vector_indexes(ws)) == ["Span[dense]"]


def test_a_rebuild_rebuilds_each_engine_dense_index_present():
    s, rec = _store()
    took = s.rebuild_vector_indexes()
    assert list(took) == ["Chunk[dense]", "Span[dense]"] and all(t >= 0 for t in took.values())
    assert rec.sql == ["REBUILD INDEX `Chunk[dense]`", "REBUILD INDEX `Span[dense]`"]


def test_the_api_rebuilds_the_workspace_store():
    s, _ = _store()
    ws = WorkspaceHandle(s, EngineConfig(store=StoreConfig(host="x", port="0", user="u", password="p")), "w")
    assert list(rebuild_vector_indexes(ws)) == ["Chunk[dense]", "Span[dense]"]


# --- live -----------------------------------------------------------------------------------------------------------

@pytest.mark.store
def test_live_warming_a_store_with_spans_times_the_span_index():
    s = ArcadeDBStore.from_env(database=_DB, reset=True)
    try:
        s.ensure_schema()
        rng = random.Random(7)
        s._db.execute_transaction([
            f"INSERT INTO Span SET span_id = 's{i}', document_id = 'd', "
            f"dense = {[rng.uniform(-1, 1) for _ in range(BGE_M3_DENSE_DIM)]}" for i in range(20)])
        took = s.warm_vector_indexes()
        assert list(took) == ["Span[dense]"] and took["Span[dense]"] >= 0
        assert list(s.warm_vector_indexes()) == ["Span[dense]"]  # repeatable
        assert list(s.rebuild_vector_indexes()) == ["Chunk[dense]", "Span[dense]"]
        (row,) = s._query("SELECT dense FROM Span WHERE span_id = 's3'")
        assert s.span_dense_search(row["dense"], k=1)[0]["span_id"] == "s3"  # the rebuilt index serves
    finally:
        s.drop()
        s.close()
