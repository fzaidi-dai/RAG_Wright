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

import json
import os
from typing import Any, Iterable

from arcadedb_python import DatabaseDao, SyncClient

from rag_wright.contracts.chunk import BGE_M3_DENSE_DIM, ChunkRecord, MetadataValue
from rag_wright.contracts.contract_meta import ContractRecord
from rag_wright.contracts.property import FOLIO_SUBJECT_IRI, ClausePropertyRecord
from rag_wright.contracts.span import SpanRecord
from rag_wright.store.seam import GraphEdge, GraphNode

CHUNK_TYPE = "Chunk"
ENTITY_TYPE = "Entity"
REL_EDGE_TYPE = "Relationship"  # entity -> entity relationship edge (the graph's primary content)
MENTIONS_EDGE_TYPE = "Mentions"  # chunk -> entity provenance edge (FR-S.1: chunk and entities connect)

# FR-R (ADR-0025/0026) property graph: clause node -> typed property edge -> shared property-value node.
# Distinct from the generic Entity graph. Value nodes are deduped by (dimension,value); the controlled
# vocabulary is already canonical, so no entity-resolution clustering is needed.
CLAUSE_TYPE = "Clause"
PROPVALUE_TYPE = "PropertyValue"
PROPERTY_EDGE_TYPE = "HasProperty"  # Clause -> PropertyValue, carrying the assertion's provenance

# Candidates fetched per leg before fusion. RRF reorders within this pool, so it is set well above a
# typical final `k` to give fusion (and any metadata filter) room to work; the fused list is then
# cut to `k`. Tuned at GATE-2 against the golden set if recall calls for it.
DEFAULT_CANDIDATE_POOL = 100

SPAN_TYPE = "Span"  # FR-R (ADR-0025): the operative-span hybrid index; dense+sparse over the span text
CONTRACT_TYPE = "Contract"  # CU-B3 (ADR-0029): contract-level metadata (the CUAD document lookup unit)

# Expected index names follow ArcadeDB's `Type[prop]` / `Type[p1,p2]` convention.
_DENSE_INDEX = f"{CHUNK_TYPE}[dense]"
_SPARSE_INDEX = f"{CHUNK_TYPE}[sparse_indices,sparse_weights]"
_CHUNK_ID_INDEX = f"{CHUNK_TYPE}[chunk_id]"
_ENTITY_ID_INDEX = f"{ENTITY_TYPE}[entity_id]"
_SPAN_ID_INDEX = f"{SPAN_TYPE}[span_id]"
_SPAN_DENSE_INDEX = f"{SPAN_TYPE}[dense]"
_SPAN_SPARSE_INDEX = f"{SPAN_TYPE}[sparse_indices,sparse_weights]"
_CLAUSE_ID_INDEX = f"{CLAUSE_TYPE}[clause_id]"
_PROPVALUE_KEY_INDEX = f"{PROPVALUE_TYPE}[value_key]"
_CONTRACT_ID_INDEX = f"{CONTRACT_TYPE}[contract_id]"


def _property_value_key(dimension: str, value: str) -> str:
    """The shared `PropertyValue` node identity: the canonical (dimension, value). The controlled
    vocabulary is already canonical, so dedup across clauses is a deterministic upsert by this key."""
    return f"{dimension}:{value}"


def _sql_str(value: str) -> str:
    """A single-quoted ArcadeDB SQL string literal (backslash, quote, and control whitespace escaped).

    ArcadeDB's SQL tokenizer rejects a raw newline / carriage-return / tab inside a string literal (a
    "token recognition error at ..."), so those are backslash-escaped alongside the quote and backslash.
    Backslash is escaped first so the escapes added afterward each carry a single backslash. Discovered
    ingesting ACORD's multi-paragraph clauses (T33): without this, every clause containing a newline
    silently dead-lettered.
    """
    return (
        "'"
        + value.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
        + "'"
    )


def _float_array(values: Iterable[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in values) + "]"


def _str_array(values: Iterable[str]) -> str:
    return "[" + ",".join(_sql_str(v) for v in values) + "]"


def _sql_literal(value: MetadataValue) -> str:
    """A SQL literal for a filterable metadata scalar (bool checked before int: `bool` subclasses `int`)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return _sql_str(str(value))


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
            self._command(f"CREATE PROPERTY {ENTITY_TYPE}.entity_id STRING")  # node key (CIK or surrogate)
            self._command(f"CREATE PROPERTY {ENTITY_TYPE}.chunk_id STRING")
            self._command(f"CREATE PROPERTY {ENTITY_TYPE}.cik STRING")  # canonical CIK, or '' if unlinked
            self._command(f"CREATE PROPERTY {ENTITY_TYPE}.name STRING")
            self._command(f"CREATE PROPERTY {ENTITY_TYPE}.entity_type STRING")
            self._command(f"CREATE PROPERTY {ENTITY_TYPE}.confidence STRING")
        if REL_EDGE_TYPE not in types:
            self._command(f"CREATE EDGE TYPE {REL_EDGE_TYPE}")
        if MENTIONS_EDGE_TYPE not in types:
            self._command(f"CREATE EDGE TYPE {MENTIONS_EDGE_TYPE}")
        if SPAN_TYPE not in types:  # FR-R (ADR-0025): operative-span hybrid index
            self._command(f"CREATE VERTEX TYPE {SPAN_TYPE}")
            self._command(f"CREATE PROPERTY {SPAN_TYPE}.span_id STRING")
            self._command(f"CREATE PROPERTY {SPAN_TYPE}.parent_chunk_id STRING")
            self._command(f"CREATE PROPERTY {SPAN_TYPE}.parent_okf_path STRING")
            self._command(f"CREATE PROPERTY {SPAN_TYPE}.span_index INTEGER")
            self._command(f"CREATE PROPERTY {SPAN_TYPE}.text STRING")
            self._command(f"CREATE PROPERTY {SPAN_TYPE}.function STRING")  # the function-classifier tag (T56)
            self._command(f"CREATE PROPERTY {SPAN_TYPE}.dense ARRAY_OF_FLOATS")
            self._command(f"CREATE PROPERTY {SPAN_TYPE}.sparse_indices ARRAY_OF_INTEGERS")
            self._command(f"CREATE PROPERTY {SPAN_TYPE}.sparse_weights ARRAY_OF_FLOATS")
            self._command(f"CREATE PROPERTY {SPAN_TYPE}.contract_id STRING")  # CU-B2: within-contract filter
            self._command(f"CREATE PROPERTY {SPAN_TYPE}.doc_start INTEGER")  # CU-B2: doc-absolute char offset
            self._command(f"CREATE PROPERTY {SPAN_TYPE}.doc_end INTEGER")  # CU-B2: exclusive (citation)
        if CLAUSE_TYPE not in types:  # FR-R (ADR-0026): the property graph (clause node)
            self._command(f"CREATE VERTEX TYPE {CLAUSE_TYPE}")
            self._command(f"CREATE PROPERTY {CLAUSE_TYPE}.clause_id STRING")  # parent chunk / OKF pointer
            self._command(f"CREATE PROPERTY {CLAUSE_TYPE}.function STRING")
            self._command(f"CREATE PROPERTY {CLAUSE_TYPE}.folio_iri STRING")
        if PROPVALUE_TYPE not in types:  # shared, deduped (dimension,value) node
            self._command(f"CREATE VERTEX TYPE {PROPVALUE_TYPE}")
            self._command(f"CREATE PROPERTY {PROPVALUE_TYPE}.value_key STRING")
            self._command(f"CREATE PROPERTY {PROPVALUE_TYPE}.dimension STRING")
            self._command(f"CREATE PROPERTY {PROPVALUE_TYPE}.value STRING")
            self._command(f"CREATE PROPERTY {PROPVALUE_TYPE}.folio_iri STRING")
        if PROPERTY_EDGE_TYPE not in types:  # Clause -> PropertyValue (carries the assertion provenance)
            self._command(f"CREATE EDGE TYPE {PROPERTY_EDGE_TYPE}")
        if CONTRACT_TYPE not in types:  # CU-B3: contract metadata (the CUAD document lookup unit)
            self._command(f"CREATE VERTEX TYPE {CONTRACT_TYPE}")
            self._command(f"CREATE PROPERTY {CONTRACT_TYPE}.contract_id STRING")
            self._command(f"CREATE PROPERTY {CONTRACT_TYPE}.name STRING")
            self._command(f"CREATE PROPERTY {CONTRACT_TYPE}.agreement_type STRING")
            self._command(f"CREATE PROPERTY {CONTRACT_TYPE}.parties_json STRING")  # json.dumps(parties)
            self._command(f"CREATE PROPERTY {CONTRACT_TYPE}.agreement_date STRING")
            self._command(f"CREATE PROPERTY {CONTRACT_TYPE}.effective_date STRING")
            self._command(f"CREATE PROPERTY {CONTRACT_TYPE}.source_doc_id STRING")
            self._command(f"CREATE PROPERTY {CONTRACT_TYPE}.content_hash STRING")
            self._command(f"CREATE PROPERTY {CONTRACT_TYPE}.page_count INTEGER")

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
        if _SPAN_ID_INDEX not in indexes:
            self._command(f"CREATE INDEX ON {SPAN_TYPE} (span_id) UNIQUE")
        if _SPAN_DENSE_INDEX not in indexes:
            self._command(
                f"CREATE INDEX ON {SPAN_TYPE} (dense) LSM_VECTOR "
                f"METADATA {{ dimensions: {BGE_M3_DENSE_DIM}, similarity: 'COSINE' }}"
            )
        if _SPAN_SPARSE_INDEX not in indexes:
            self._command(
                f"CREATE INDEX ON {SPAN_TYPE} (sparse_indices, sparse_weights) LSM_SPARSE_VECTOR"
            )
        if _CLAUSE_ID_INDEX not in indexes:
            self._command(f"CREATE INDEX ON {CLAUSE_TYPE} (clause_id) UNIQUE")
        if _PROPVALUE_KEY_INDEX not in indexes:
            self._command(f"CREATE INDEX ON {PROPVALUE_TYPE} (value_key) UNIQUE")
        if _CONTRACT_ID_INDEX not in indexes:
            self._command(f"CREATE INDEX ON {CONTRACT_TYPE} (contract_id) UNIQUE")

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

    # --- operative-span write/search (FR-R, ADR-0025) -------------------------------------------

    def upsert_span(self, record: SpanRecord) -> None:
        """Upsert an operative-span record by `span_id` (dense + sparse over the span text). Same sparse
        decomposition into the two parallel arrays the `LSM_SPARSE_VECTOR` index binds as `upsert_chunk`."""
        token_ids = sorted(record.sparse_vector)  # deterministic order across the paired arrays
        dense = _float_array(record.dense_vector)
        sparse_indices = "[" + ",".join(str(i) for i in token_ids) + "]"
        sparse_weights = _float_array(record.sparse_vector[i] for i in token_ids)
        doc_start = "null" if record.doc_start is None else int(record.doc_start)
        doc_end = "null" if record.doc_end is None else int(record.doc_end)
        self._command(
            f"UPDATE {SPAN_TYPE} SET"
            f" span_id = {_sql_str(record.span_id)},"
            f" parent_chunk_id = {_sql_str(record.parent_chunk_id)},"
            f" parent_okf_path = {_sql_str(record.parent_okf_path)},"
            f" span_index = {int(record.span_index)},"
            f" text = {_sql_str(record.text)},"
            f" function = {_sql_str(record.function)},"
            f" dense = {dense},"
            f" sparse_indices = {sparse_indices},"
            f" sparse_weights = {sparse_weights},"
            f" contract_id = {_sql_str(record.contract_id)},"  # CU-B2: citation + within-contract filter
            f" doc_start = {doc_start},"
            f" doc_end = {doc_end}"
            f" UPSERT WHERE span_id = {_sql_str(record.span_id)}"
        )

    def upsert_contract(self, record: ContractRecord) -> None:
        """CU-B3: upsert a contract's metadata by `contract_id` (the CUAD document lookup unit). `parties`
        is stored JSON-encoded; `page_count` may be null."""
        page = "null" if record.page_count is None else int(record.page_count)
        self._command(
            f"UPDATE {CONTRACT_TYPE} SET"
            f" contract_id = {_sql_str(record.contract_id)},"
            f" name = {_sql_str(record.name)},"
            f" agreement_type = {_sql_str(record.agreement_type)},"
            f" parties_json = {_sql_str(json.dumps(record.parties))},"
            f" agreement_date = {_sql_str(record.agreement_date)},"
            f" effective_date = {_sql_str(record.effective_date)},"
            f" source_doc_id = {_sql_str(record.source_doc_id)},"
            f" content_hash = {_sql_str(record.content_hash)},"
            f" page_count = {page}"
            f" UPSERT WHERE contract_id = {_sql_str(record.contract_id)}"
        )

    def contract_by_id(self, contract_id: str) -> dict | None:
        """CU-B3: look up a contract's metadata by id (the row, or None if absent)."""
        rows = self._query(
            f"SELECT contract_id, name, agreement_type, parties_json, agreement_date, effective_date,"
            f" source_doc_id, content_hash, page_count FROM {CONTRACT_TYPE}"
            f" WHERE contract_id = {_sql_str(contract_id)}"
        )
        return rows[0] if rows else None

    def spans_by_contract(self, contract_id: str, functions: list[str]) -> list[dict]:
        """CU-B3: the within-contract typed filter -- every span in `contract_id` whose `function` is in
        `functions`, ordered by document position (the CUAD serve retrieval; empty `functions` -> []).
        Returns citation-ready rows (span_id, parent pointer, text, function, doc offsets)."""
        if not functions:
            return []
        return self._query(
            f"SELECT span_id, parent_chunk_id, parent_okf_path, span_index, text, function,"
            f" contract_id, doc_start, doc_end FROM {SPAN_TYPE}"
            f" WHERE contract_id = {_sql_str(contract_id)} AND function IN {_str_array(functions)}"
            f" ORDER BY doc_start"
        )

    def all_spans_by_contract(self, contract_id: str) -> list[dict]:
        """CU-C2: EVERY span in a contract (all functions incl NONE), with its dense vector, ordered by
        document position. For the out-of-taxonomy semantic fallback: the contract is small (hundreds of
        spans), so ranking happens in Python -- a global ANN + contract filter would miss, since one
        contract is ~1% of the corpus. Returns citation-ready rows plus `dense`."""
        return self._query(
            f"SELECT span_id, parent_chunk_id, span_index, text, function, contract_id,"
            f" doc_start, doc_end, dense FROM {SPAN_TYPE}"
            f" WHERE contract_id = {_sql_str(contract_id)} ORDER BY doc_start"
        )

    def span_hybrid_search(
        self,
        dense_query: list[float],
        sparse_query: dict[int, float],
        *,
        k: int,
        function: str | None = None,
    ) -> list[dict]:
        """RRF-fused dense+sparse search over the `Span` index, optionally restricted to one `function` tag
        (the function-classifier's routing filter, FR-R). Mirrors `hybrid_search`; returns span_id + the
        parent pointer so the caller can follow the span back to its clause for the rerank stage."""
        leg_k = max(k, DEFAULT_CANDIDATE_POOL)
        token_ids = sorted(sparse_query)
        sparse_indices = "[" + ",".join(str(i) for i in token_ids) + "]"
        sparse_weights = _float_array(sparse_query[i] for i in token_ids)
        dense = _float_array(dense_query)
        fused = (
            "SELECT expand(`vector.fuse`("
            f"`vector.neighbors`('{_SPAN_DENSE_INDEX}', {dense}, {leg_k}), "
            f"`vector.sparseNeighbors`('{_SPAN_SPARSE_INDEX}', {sparse_indices}, {sparse_weights}, {leg_k}), "
            "{ fusion: 'RRF' }))"
        )
        where = f" WHERE function = {_sql_str(function)}" if function else ""
        return self._query(
            f"SELECT span_id, parent_chunk_id, parent_okf_path, function FROM ({fused}){where} LIMIT {k}"
        )

    def chunk_count(self) -> int:
        rows = self._query(f"SELECT count(*) AS n FROM {CHUNK_TYPE}")
        return int(rows[0]["n"]) if rows else 0

    # --- query-side (T21) -----------------------------------------------------------------------

    def hybrid_search(
        self,
        dense_query: list[float],
        sparse_query: dict[int, float],
        *,
        k: int,
        filters: dict[str, MetadataValue] | None = None,
    ) -> list[dict]:
        """Server-side RRF fusion of the dense and sparse legs, honoring equality metadata filters.

        Grounded and proven end to end at T14: `vector.fuse` fuses a dense `vector.neighbors` leg and a
        sparse `vector.sparseNeighbors` leg with the RRF strategy; `expand` flattens the fused list into
        rows. Each leg is fetched to `DEFAULT_CANDIDATE_POOL` (or `k` if larger) so fusion and the filter
        have a real pool to work over; the fused, ranked result is then filtered and cut to `k`. Dotted
        function names are backtick-quoted. The `arcadedb-python` API does not wrap these functions
        (ADR-0007/ADR-0008), so they are issued through the grounded `query()` method.
        """
        leg_k = max(k, DEFAULT_CANDIDATE_POOL)
        token_ids = sorted(sparse_query)  # deterministic order across the paired arrays
        sparse_indices = "[" + ",".join(str(i) for i in token_ids) + "]"
        sparse_weights = _float_array(sparse_query[i] for i in token_ids)
        dense = _float_array(dense_query)
        fused = (
            "SELECT expand(`vector.fuse`("
            f"`vector.neighbors`('{_DENSE_INDEX}', {dense}, {leg_k}), "
            f"`vector.sparseNeighbors`('{_SPARSE_INDEX}', {sparse_indices}, {sparse_weights}, {leg_k}), "
            "{ fusion: 'RRF' }))"
        )
        where = ""
        if filters:
            clauses = " AND ".join(f"{col} = {_sql_literal(v)}" for col, v in filters.items())
            where = f" WHERE {clauses}"
        return self._query(f"SELECT chunk_id, source_doc_id FROM ({fused}){where} LIMIT {k}")

    # --- graph-write (T25) ----------------------------------------------------------------------

    def write_graph(self, nodes: list[GraphNode], edges: list[GraphEdge]) -> None:
        """Upsert entity nodes and create relationship edges in ONE transaction (FR-S.1). Each entity
        is connected to its source chunk by a `Mentions` edge (for chunks that exist), so a chunk and
        its extracted entities land together. Uses `execute_transaction` so a failure rolls back whole."""
        statements: list[str] = []
        for node in nodes:  # nodes first, so edge endpoints exist within the transaction
            statements.append(
                f"UPDATE {ENTITY_TYPE} SET"
                f" entity_id = {_sql_str(node.node_key)},"
                f" cik = {_sql_str(node.entity_id)},"
                f" name = {_sql_str(node.name)},"
                f" entity_type = {_sql_str(node.entity_type)},"
                f" confidence = {_sql_str(node.confidence)},"
                f" chunk_id = {_sql_str(node.chunk_id)}"
                f" UPSERT WHERE entity_id = {_sql_str(node.node_key)}"
            )
        existing_chunks = self._existing_chunks({node.chunk_id for node in nodes})
        for node in nodes:
            if node.chunk_id in existing_chunks:  # connect chunk -> entity (provenance edge)
                statements.append(
                    f"CREATE EDGE {MENTIONS_EDGE_TYPE}"
                    f" FROM (SELECT FROM {CHUNK_TYPE} WHERE chunk_id = {_sql_str(node.chunk_id)})"
                    f" TO (SELECT FROM {ENTITY_TYPE} WHERE entity_id = {_sql_str(node.node_key)})"
                )
        for edge in edges:
            statements.append(
                f"CREATE EDGE {REL_EDGE_TYPE}"
                f" FROM (SELECT FROM {ENTITY_TYPE} WHERE entity_id = {_sql_str(edge.source_key)})"
                f" TO (SELECT FROM {ENTITY_TYPE} WHERE entity_id = {_sql_str(edge.target_key)})"
                f" SET relationship_type = {_sql_str(edge.relationship_type)},"
                f" confidence = {_sql_str(edge.confidence)}, chunk_id = {_sql_str(edge.chunk_id)}"
            )
        if statements:
            self._db.execute_transaction(statements)

    def graph_counts(self) -> dict[str, int]:
        entities = self._query(f"SELECT count(*) AS n FROM {ENTITY_TYPE}")
        rels = self._query(f"SELECT count(*) AS n FROM {REL_EDGE_TYPE}")
        return {
            "entities": int(entities[0]["n"]) if entities else 0,
            "relationships": int(rels[0]["n"]) if rels else 0,
        }

    def graph_neighbors(
        self, entity_id: str, *, relationship_type: str, max_hops: int
    ) -> list[dict]:
        """Traverse via ArcadeDB `MATCH` over `Relationship` edges (grounded live, T26): `bothE` binds
        each edge (so its `chunk_id`/`confidence` are cited) and `bothV` the reached entity. One-hop and
        two-hop are separate MATCH queries; `$matched` de-dups the two-hop return to the start. Braces
        are concatenated in (they clash with f-string interpolation)."""
        eid = _sql_str(entity_id)
        rel = _sql_str(relationship_type)
        paths: list[dict] = []

        one_hop = (
            "MATCH {type: " + ENTITY_TYPE + ", as: a, where: (entity_id = " + eid + ")}"
            ".bothE('" + REL_EDGE_TYPE + "'){as: e, where: (relationship_type = " + rel + ")}"
            ".bothV(){as: b, where: (entity_id <> " + eid + ")}"
            " RETURN b.entity_id AS target_id, b.name AS target_name,"
            " e.chunk_id AS c1, e.confidence AS cf1"
        )
        for row in self._query(one_hop):
            paths.append({
                "target_id": row["target_id"], "target_name": row["target_name"],
                "path_entity_ids": [entity_id, row["target_id"]],
                "path_chunk_ids": [row["c1"]], "path_confidences": [row["cf1"]], "hops": 1,
            })

        if max_hops >= 2:
            two_hop = (
                "MATCH {type: " + ENTITY_TYPE + ", as: a, where: (entity_id = " + eid + ")}"
                ".bothE('" + REL_EDGE_TYPE + "'){as: e1, where: (relationship_type = " + rel + ")}"
                ".bothV(){as: b, where: (entity_id <> " + eid + ")}"
                ".bothE('" + REL_EDGE_TYPE + "'){as: e2, where: (relationship_type = " + rel + ")}"
                ".bothV(){as: cc, where: (entity_id <> " + eid
                + " and entity_id <> $matched.b.entity_id)}"
                " RETURN b.entity_id AS mid_id, e1.chunk_id AS e1c, e1.confidence AS e1cf,"
                " cc.entity_id AS target_id, cc.name AS target_name,"
                " e2.chunk_id AS e2c, e2.confidence AS e2cf"
            )
            for row in self._query(two_hop):
                paths.append({
                    "target_id": row["target_id"], "target_name": row["target_name"],
                    "path_entity_ids": [entity_id, row["mid_id"], row["target_id"]],
                    "path_chunk_ids": [row["e1c"], row["e2c"]],
                    "path_confidences": [row["e1cf"], row["e2cf"]], "hops": 2,
                })
        return paths

    # --- property graph (T57c, FR-R) ------------------------------------------------------------

    def write_property_graph(self, record: ClausePropertyRecord) -> None:
        """Write the clause node + its property-value nodes + the typed property edges in ONE transaction
        (FR-S.1). Value nodes are shared/deduped by (dimension,value) -- the vocabulary is canonical, so no
        entity-resolution clustering is needed. Idempotent by a content-hash gate: `clause_id` embeds the
        content hash (FR-S.2), so a committed clause node means identical content and hence identical
        assertions (a changed clause is a NEW node); if the clause already exists we skip -- which also
        means the edge writes only ever run once per clause, so plain `CREATE EDGE` cannot duplicate. Every
        edge carries the assertion's confidence + span_id + chunk_id provenance (FR-S.4 / FR-Q.6)."""
        cid = _sql_str(record.clause_id)
        if self._query(f"SELECT clause_id FROM {CLAUSE_TYPE} WHERE clause_id = {cid} LIMIT 1"):
            return  # already populated (content-hash gate): a committed clause_id -> identical assertions
        statements: list[str] = [
            f"UPDATE {CLAUSE_TYPE} SET clause_id = {cid}, function = {_sql_str(record.function)},"
            f" folio_iri = {_sql_str(record.folio_iri)} UPSERT WHERE clause_id = {cid}",
        ]
        for a in record.assertions:
            key = _property_value_key(a.dimension.value, a.value)
            key_sql = _sql_str(key)
            statements.append(  # shared value node: upsert by canonical (dimension,value) key
                f"UPDATE {PROPVALUE_TYPE} SET value_key = {key_sql},"
                f" dimension = {_sql_str(a.dimension.value)}, value = {_sql_str(a.value)},"
                f" folio_iri = {_sql_str(FOLIO_SUBJECT_IRI.get(a.value, ''))}"
                f" UPSERT WHERE value_key = {key_sql}"
            )
            statements.append(
                f"CREATE EDGE {PROPERTY_EDGE_TYPE}"
                f" FROM (SELECT FROM {CLAUSE_TYPE} WHERE clause_id = {cid})"
                f" TO (SELECT FROM {PROPVALUE_TYPE} WHERE value_key = {key_sql})"
                f" SET confidence = {_sql_str(a.confidence.value)}, span_id = {_sql_str(a.span_id)},"
                f" chunk_id = {_sql_str(str(a.provenance.chunk_id))},"
                f" source_doc_id = {_sql_str(a.provenance.source_doc_id)}"
            )
        self._db.execute_transaction(statements)

    def clear_property_graph(self) -> None:
        """Delete all property-graph records (Clause / PropertyValue / HasProperty) while LEAVING the span
        index intact -- so a property re-extraction can start from scratch without re-embedding (T58 resume
        control). Edges first (UNSAFE bypasses the edge-safety check this dialect requires), then vertices."""
        self._command(f"DELETE FROM {PROPERTY_EDGE_TYPE} UNSAFE")
        self._command(f"DELETE FROM {CLAUSE_TYPE}")
        self._command(f"DELETE FROM {PROPVALUE_TYPE}")

    def property_graph_counts(self) -> dict[str, int]:
        """Counts for introspection/tests: clauses, shared property-value nodes, and property edges."""
        clauses = self._query(f"SELECT count(*) AS n FROM {CLAUSE_TYPE}")
        values = self._query(f"SELECT count(*) AS n FROM {PROPVALUE_TYPE}")
        edges = self._query(f"SELECT count(*) AS n FROM {PROPERTY_EDGE_TYPE}")
        return {
            "clauses": int(clauses[0]["n"]) if clauses else 0,
            "property_values": int(values[0]["n"]) if values else 0,
            "property_edges": int(edges[0]["n"]) if edges else 0,
        }

    def clause_property_values(self, clause_id: str) -> list[dict]:
        """The property values a clause asserts, each with the edge's provenance (dimension, value,
        confidence, span_id) -- the readback for tests and the shape T58's query builds on."""
        q = (
            "MATCH {type: " + CLAUSE_TYPE + ", as: c, where: (clause_id = " + _sql_str(clause_id) + ")}"
            ".outE('" + PROPERTY_EDGE_TYPE + "'){as: e}.inV(){as: v}"
            " RETURN v.dimension AS dimension, v.value AS value, e.confidence AS confidence,"
            " e.span_id AS span_id"
        )
        return self._query(q)

    def _existing_chunks(self, chunk_ids: set[str]) -> set[str]:
        if not chunk_ids:
            return set()
        rows = self._query(
            f"SELECT chunk_id FROM {CHUNK_TYPE} WHERE chunk_id IN {_str_array(sorted(chunk_ids))}"
        )
        return {row["chunk_id"] for row in rows}

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
