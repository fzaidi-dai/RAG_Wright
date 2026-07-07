"""A-T1 (T14): ArcadeDB `vector.fuse` RRF hybrid foundation test (FR-C.3 dep, risk 1).

Writes a few chunk records into the real T13 schema and runs a server-side hybrid query end to end:
a dense `vector.neighbors` leg and a sparse `vector.sparseNeighbors` leg fused by `vector.fuse` with
the RRF strategy, honoring a metadata filter. Early signal for GATE-2: does ArcadeDB hybrid
retrieval behave as documented, or do we lean toward the LanceDB fallback?

SQL grounded against the official ArcadeDB docs and confirmed on the live 26.7.1 server (ADR-0007);
the arcadedb-python API does not wrap these functions, so they are issued through its grounded
`query()` method. Opt-in and live: marked `store`, run with `-m store` (needs the container up).
"""

from __future__ import annotations

import os

import pytest
from arcadedb_python import DatabaseDao, SyncClient

from rag_wright.contracts.chunk import BGE_M3_DENSE_DIM
from rag_wright.store.arcadedb import ArcadeDBStore

pytestmark = pytest.mark.store

_DB = "ragwright_hybrid_test"


def _client() -> SyncClient:
    return SyncClient(
        os.environ["ARCADEDB_HOST"],
        os.environ["ARCADEDB_PORT"],
        username=os.environ["ARCADEDB_USER"],
        password=os.environ["ARCADEDB_PASSWORD"],
    )


def _dense(*nonzero: tuple[int, float]) -> list[float]:
    """A BGE-M3-length dense vector with a few non-zero positions (keeps the literal small)."""
    v = [0.0] * BGE_M3_DENSE_DIM
    for i, w in nonzero:
        v[i] = w
    return v


# Four records across two source docs. Dense favours axis 0 (c1 exact, c2 close); the sparse query
# token set {10,30} matches c2 exactly and c1/c4 partially. So c1 wins the dense leg, c2 wins the
# sparse leg, and c3 (weak on both) should sink under fusion.
_ROWS = [
    ("c1", "docA", _dense((0, 1.0)), [10, 20], [0.9, 0.3]),
    ("c2", "docA", _dense((0, 0.8), (1, 0.2)), [10, 30], [0.8, 0.6]),
    ("c3", "docB", _dense((1, 1.0)), [20, 40], [0.7, 0.5]),
    ("c4", "docB", _dense((2, 1.0)), [30, 40], [0.9, 0.2]),
]

_DENSE_Q = _dense((0, 1.0))
_Q_IDX, _Q_VAL = [10, 30], [0.7, 0.7]


def _fuse_sql(limit: int) -> str:
    # Dotted function names are backtick-quoted. `expand` flattens the fused list into rows.
    return (
        "SELECT expand(`vector.fuse`("
        f"`vector.neighbors`('Chunk[dense]', {_DENSE_Q}, 4), "
        f"`vector.sparseNeighbors`('Chunk[sparse_indices,sparse_weights]', {_Q_IDX}, {_Q_VAL}, 4), "
        f"{{ fusion: 'RRF' }})) LIMIT {limit}"
    )


@pytest.fixture
def hybrid_db():
    client = _client()
    if DatabaseDao.exists(client, _DB):
        DatabaseDao.delete(client, _DB)
    DatabaseDao.create(client, _DB)
    ArcadeDBStore(client, _DB).ensure_schema()  # the real T13 schema (dense + sparse hybrid indexes)
    dao = DatabaseDao(client, _DB)
    for chunk_id, doc, dense, s_idx, s_wt in _ROWS:
        dao.query(
            "sql",
            f"INSERT INTO Chunk SET chunk_id='{chunk_id}', source_doc_id='{doc}', "
            f"dense={dense}, sparse_indices={s_idx}, sparse_weights={s_wt}",
            is_command=True,
        )
    yield dao
    DatabaseDao.delete(client, _DB)


def test_vector_fuse_returns_a_sensible_hybrid_ranking(hybrid_db):
    rows = hybrid_db.query("sql", _fuse_sql(4))
    ids = [r["chunk_id"] for r in rows]

    assert ids  # server-side RRF fusion returned a ranked list
    # the two docA records (c1 wins the dense leg, c2 the sparse leg) fuse above the weak docB
    # records; c3 (near-zero on both legs) does not reach the top two.
    assert set(ids[:2]) == {"c1", "c2"}
    assert "c2" in ids[:2]  # the record strong on BOTH legs survives fusion near the top
    assert "c3" not in ids[:2]


def test_hybrid_query_honors_a_metadata_filter(hybrid_db):
    sql = f"SELECT chunk_id, source_doc_id FROM ({_fuse_sql(10)}) WHERE source_doc_id = 'docA'"
    rows = hybrid_db.query("sql", sql)

    assert rows  # the filter did not empty the result
    assert {r["source_doc_id"] for r in rows} == {"docA"}  # only the filtered source survives
    assert {r["chunk_id"] for r in rows} <= {"c1", "c2"}
