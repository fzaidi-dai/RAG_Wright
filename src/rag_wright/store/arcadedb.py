"""The ArcadeDB implementation of the store seam (T13, FR-S.1, FR-S.5).

One multi-model database holds both the hybrid retrieval index (the `Chunk` vertex type, carrying
the dense summary vector and the sparse full-text vector) and the knowledge graph (the `Entity`
vertex type), so a chunk and its extracted entities share one store and one `chunk_id` with no
cross-store join (FR-S.1). The hybrid index has two legs: a dense `LSM_VECTOR` (HNSW) index over the
summary vector and a sparse `LSM_SPARSE_VECTOR` index over the full-text vector.

Grounded against `arcadedb_python` 0.4.0 (`SyncClient`, `DatabaseDao`) and the live ArcadeDB
26.7.2 server: the sparse index requires the sparse vector stored as two parallel arrays,
`sparse_indices` (ARRAY_OF_INTEGERS) and `sparse_weights` (ARRAY_OF_FLOATS), not a single map, so
T3's `sparse_vector: dict[int, float]` is decomposed at the store boundary (T20 writes it). ArcadeDB
rejects `CREATE ... IF NOT EXISTS` in this dialect position, so idempotency is by schema
introspection: create only what is absent.
"""

from __future__ import annotations

import os
from typing import Any, Iterable

from arcadedb_python import DatabaseDao, SyncClient

from rag_wright.contracts.chunk import BGE_M3_DENSE_DIM, ChunkRecord

CHUNK_TYPE = "Chunk"
ENTITY_TYPE = "Entity"

# Expected index names follow ArcadeDB's `Type[prop]` / `Type[p1,p2]` convention.
_DENSE_INDEX = f"{CHUNK_TYPE}[dense]"
_SPARSE_INDEX = f"{CHUNK_TYPE}[sparse_indices,sparse_weights]"
_CHUNK_ID_INDEX = f"{CHUNK_TYPE}[chunk_id]"
_ENTITY_ID_INDEX = f"{ENTITY_TYPE}[entity_id]"


def _sql_str(value: str) -> str:
    """A single-quoted ArcadeDB SQL string literal (backslash and quote escaped)."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _float_array(values: Iterable[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in values) + "]"


def _str_array(values: Iterable[str]) -> str:
    return "[" + ",".join(_sql_str(v) for v in values) + "]"


class ArcadeDBStore:
    """The default store: schema management over a single ArcadeDB database."""

    def __init__(self, client: SyncClient, database: str) -> None:
        self._client = client
        self._database = database
        self._db = DatabaseDao(client, database)

    @classmethod
    def from_env(cls, *, database: str | None = None, reset: bool = False) -> "ArcadeDBStore":
        """Build a store from `ARCADEDB_*` env, creating the database if absent.

        `database` overrides `ARCADEDB_DATABASE` (used to point tests at a scratch database).
        `reset=True` drops and recreates the database first, for a clean-slate test.
        """
        client = SyncClient(
            os.environ["ARCADEDB_HOST"],
            os.environ["ARCADEDB_PORT"],
            username=os.environ["ARCADEDB_USER"],
            password=os.environ["ARCADEDB_PASSWORD"],
        )
        name = database or os.environ["ARCADEDB_DATABASE"]
        if reset and DatabaseDao.exists(client, name):
            DatabaseDao.delete(client, name)
        if not DatabaseDao.exists(client, name):
            DatabaseDao.create(client, name)
        return cls(client, name)

    # --- seam surface ---------------------------------------------------------------------------

    def ensure_schema(self) -> None:
        """Create the chunk-record and graph-node types and the hybrid indexes, idempotently."""
        types = self.type_names()
        if CHUNK_TYPE not in types:
            self._command(f"CREATE VERTEX TYPE {CHUNK_TYPE}")
            self._command(f"CREATE PROPERTY {CHUNK_TYPE}.chunk_id STRING")
            self._command(f"CREATE PROPERTY {CHUNK_TYPE}.source_doc_id STRING")
            self._command(f"CREATE PROPERTY {CHUNK_TYPE}.dense ARRAY_OF_FLOATS")
            self._command(f"CREATE PROPERTY {CHUNK_TYPE}.sparse_indices ARRAY_OF_INTEGERS")
            self._command(f"CREATE PROPERTY {CHUNK_TYPE}.sparse_weights ARRAY_OF_FLOATS")
        if ENTITY_TYPE not in types:
            self._command(f"CREATE VERTEX TYPE {ENTITY_TYPE}")
            self._command(f"CREATE PROPERTY {ENTITY_TYPE}.entity_id STRING")
            self._command(f"CREATE PROPERTY {ENTITY_TYPE}.chunk_id STRING")

        indexes = self.index_names()
        if _CHUNK_ID_INDEX not in indexes:
            self._command(f"CREATE INDEX ON {CHUNK_TYPE} (chunk_id) UNIQUE")
        if _DENSE_INDEX not in indexes:
            self._command(
                f"CREATE INDEX ON {CHUNK_TYPE} (dense) LSM_VECTOR "
                f"METADATA {{ dimensions: {BGE_M3_DENSE_DIM}, similarity: 'COSINE' }}"
            )
        if _SPARSE_INDEX not in indexes:
            self._command(
                f"CREATE INDEX ON {CHUNK_TYPE} (sparse_indices, sparse_weights) LSM_SPARSE_VECTOR"
            )
        if _ENTITY_ID_INDEX not in indexes:
            self._command(f"CREATE INDEX ON {ENTITY_TYPE} (entity_id) UNIQUE")

    def type_names(self) -> set[str]:
        return {row["name"] for row in self._query("SELECT name FROM schema:types")}

    def property_names(self, type_name: str) -> set[str]:
        for row in self._query("SELECT name, properties FROM schema:types"):
            if row.get("name") == type_name:
                return {prop["name"] for prop in row.get("properties", [])}
        return set()

    def index_names(self) -> set[str]:
        return {row["name"] for row in self._query("SELECT name FROM schema:indexes")}

    def ping(self) -> bool:
        return DatabaseDao.exists(self._client, self._database)

    def close(self) -> None:
        # The HTTP client holds no persistent connection to release.
        pass

    # --- write-side (T20) -----------------------------------------------------------------------

    def upsert_chunk(self, record: ChunkRecord) -> None:
        """Upsert a chunk record by `chunk_id`. The sparse vector is decomposed into the two parallel
        arrays the `LSM_SPARSE_VECTOR` index binds (ADR-0007); the dense vector's length is enforced
        by the `LSM_VECTOR` index."""
        chunk_id = record.chunk_id.value
        token_ids = sorted(record.sparse_vector)  # deterministic order across the paired arrays
        dense = _float_array(record.dense_vector)
        sparse_indices = "[" + ",".join(str(i) for i in token_ids) + "]"
        sparse_weights = _float_array(record.sparse_vector[i] for i in token_ids)
        self._command(
            f"UPDATE {CHUNK_TYPE} SET"
            f" chunk_id = {_sql_str(chunk_id)},"
            f" source_doc_id = {_sql_str(record.chunk_id.source_doc_id)},"
            f" summary = {_sql_str(record.summary)},"
            f" dense = {dense},"
            f" sparse_indices = {sparse_indices},"
            f" sparse_weights = {sparse_weights},"
            f" keywords = {_str_array(record.keywords)},"
            f" entity_mentions = {_str_array(record.entity_mentions)}"
            f" UPSERT WHERE chunk_id = {_sql_str(chunk_id)}"
        )

    def get_chunk(self, chunk_id: str) -> Any:
        rows = self._query(
            f"SELECT chunk_id, source_doc_id, summary FROM {CHUNK_TYPE} "
            f"WHERE chunk_id = {_sql_str(chunk_id)}"
        )
        return rows[0] if rows else None

    def chunk_count(self) -> int:
        rows = self._query(f"SELECT count(*) AS n FROM {CHUNK_TYPE}")
        return int(rows[0]["n"]) if rows else 0

    # --- test / lifecycle helper ----------------------------------------------------------------

    def drop(self) -> None:
        """Delete the database (used to reset a scratch/test database)."""
        if DatabaseDao.exists(self._client, self._database):
            DatabaseDao.delete(self._client, self._database)

    # --- internals ------------------------------------------------------------------------------

    def _command(self, sql: str) -> Any:
        return self._db.query("sql", sql, is_command=True)

    def _query(self, sql: str) -> list[dict]:
        result = self._db.query("sql", sql)
        return result if isinstance(result, list) else []
