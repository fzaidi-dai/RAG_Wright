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
from typing import Any, Iterable, Optional

from arcadedb_python import DatabaseDao, SyncClient

from rag_wright.contracts.chunk import BGE_M3_DENSE_DIM, ChunkRecord, MetadataValue
from rag_wright.contracts.compliance import Requirement
from rag_wright.contracts.contract_meta import ContractRecord
from rag_wright.contracts.property import (
    FOLIO_SUBJECT_IRI,
    ClausePropertyRecord,
    PropertyDimension,
)
from rag_wright.contracts.provenance import ConfidenceTag
from rag_wright.ontology.loader import load_typed_edges  # ADR-0067 P5a: typed-edge map from the ontology
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
PROPERTY_EDGE_TYPE = "HasProperty"  # legacy flat edge (ADR-0025/0026); superseded by the KG-3 typed edges

# KG-3 (ADR-0033): the TYPED property-edge layer that replaces the single generic HasProperty edge (KG-0
# gate Q3: build typed, retire the flat edge). Each PropertyDimension maps to its sanctioned typed edge; the
# shared PropertyValue node (deduped by value_key) is UNCHANGED -- identity preserved, so the upgrade is
# additive on nodes and rebuilt on edges. Every typed edge still carries the assertion provenance (FR-S.4)
# plus a predicate IRI (ODRL for the deontic edges, our bridge IRI otherwise).
# ADR-0067 P5a: the typed-edge map (dimension -> KG edge type) + the predicate IRIs are AUTHORITATIVE in
# contract_bridge.ttl (cbr:kgEdge / cbr:KgEdgeType); loaded here, not a Python literal. Edit the ttl to retarget.
_DIM_EDGE_STR, _EDGE_PREDICATE_IRI = load_typed_edges()
_TYPED_DIMENSION_EDGE: dict[PropertyDimension, str] = {
    PropertyDimension(dim): edge for dim, edge in _DIM_EDGE_STR.items()
}
# distinct edge types (deterministic order; used as a set / for counts + DDL, never order-dependent)
TYPED_PROPERTY_EDGE_TYPES: tuple[str, ...] = tuple(sorted(set(_TYPED_DIMENSION_EDGE.values())))


def _edge_predicate_iri(edge_type: str) -> str:
    """The predicate IRI stamped on a typed edge (ODRL for the deontic edges, the bridge IRI otherwise) --
    from contract_bridge.ttl (ADR-0067 P5a)."""
    return _EDGE_PREDICATE_IRI[edge_type]

# Candidates fetched per leg before fusion. RRF reorders within this pool, so it is set well above a
# typical final `k` to give fusion (and any metadata filter) room to work; the fused list is then
# cut to `k`. Tuned at GATE-2 against the golden set if recall calls for it.
DEFAULT_CANDIDATE_POOL = 100

SPAN_TYPE = "Span"  # FR-R (ADR-0025): the operative-span hybrid index; dense+sparse over the span text
CONTRACT_TYPE = "Contract"  # CU-B3 (ADR-0029): contract-level metadata (the CUAD document lookup unit)
PARTY_TO_EDGE_TYPE = "PartyTo"  # KG-7 (ADR-0036): Entity(party) -> Contract, the unifying link
IS_EXCEPTION_TO_EDGE_TYPE = "IsExceptionTo"  # ADR-0044: exception clause (Uncapped) -> the Cap clause it excepts
REQUIREMENT_TYPE = "Requirement"  # CC-5 (compliance §13): a deontic regulatory rule (its own DB, ragwright_compliance)

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


def _stale_property_statements(span_ids: list[str]) -> list[str]:
    """ADR-0048 Phase A mark-stale (pure): one `UPDATE ... SET confidence=AMBIGUOUS WHERE span_id IN (...) AND
    confidence <> AMBIGUOUS` per typed property edge type, for the given spans. Separated from the DB call so the
    SQL is unit-tested with no store. Empty span list -> no statements (the caller no-ops)."""
    if not span_ids:
        return []
    id_list = "[" + ",".join(_sql_str(s) for s in span_ids) + "]"
    amb = _sql_str(ConfidenceTag.AMBIGUOUS.value)
    return [
        f"UPDATE {edge_type} SET confidence = {amb} WHERE span_id IN {id_list} AND confidence <> {amb}"
        for edge_type in TYPED_PROPERTY_EDGE_TYPES
    ]


def _clause_kg_statements(record: ClausePropertyRecord) -> list[str]:
    """KG-3 (ADR-0033): the pure SQL for the TYPED clause KG -- the Clause node + one shared PropertyValue
    node per (dimension,value) + one TYPED edge per assertion. Each typed edge carries the dimension, a
    predicate IRI (ODRL for the deontic edges, our bridge IRI otherwise), and the assertion provenance
    (confidence + span_id + chunk_id + source_doc_id; FR-S.4 / FR-Q.6). Value-node identity (`value_key`) is
    unchanged from the flat graph. Separated from the DB call so the mapping is unit-tested with no store."""
    cid = _sql_str(record.clause_id)
    # ADR-0048: persist the multi-label classification as a JSON string (ranked primary-first); `function` above
    # stays the PRIMARY for query readers. Empty list -> "[]".
    functions_json = _sql_str(json.dumps(
        [{"function": f.function, "confidence": f.confidence.value} for f in record.functions]))
    statements: list[str] = [
        f"UPDATE {CLAUSE_TYPE} SET clause_id = {cid}, function = {_sql_str(record.function)},"
        f" folio_iri = {_sql_str(record.folio_iri)}, span_id = {_sql_str(record.span_id)},"
        f" functions = {functions_json}"
        f" UPSERT WHERE clause_id = {cid}",
    ]
    for a in record.assertions:
        edge_type = _TYPED_DIMENSION_EDGE[a.dimension]
        key_sql = _sql_str(_property_value_key(a.dimension.value, a.value))
        statements.append(  # shared value node: upsert by canonical (dimension,value) key + FOLIO grounding
            f"UPDATE {PROPVALUE_TYPE} SET value_key = {key_sql},"
            f" dimension = {_sql_str(a.dimension.value)}, value = {_sql_str(a.value)},"
            f" folio_iri = {_sql_str(FOLIO_SUBJECT_IRI.get(a.value, ''))}"
            f" UPSERT WHERE value_key = {key_sql}"
        )
        statements.append(
            f"CREATE EDGE {edge_type}"
            f" FROM (SELECT FROM {CLAUSE_TYPE} WHERE clause_id = {cid})"
            f" TO (SELECT FROM {PROPVALUE_TYPE} WHERE value_key = {key_sql})"
            f" SET dimension = {_sql_str(a.dimension.value)},"
            f" predicate_iri = {_sql_str(_edge_predicate_iri(edge_type))},"
            f" confidence = {_sql_str(a.confidence.value)}, span_id = {_sql_str(a.span_id)},"
            f" chunk_id = {_sql_str(str(a.provenance.chunk_id))},"
            f" source_doc_id = {_sql_str(a.provenance.source_doc_id)}"
        )
    return statements


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
            protocol=os.getenv("ARCADEDB_PROTOCOL", "http"),  # `https` for the Modal-hosted KG (EC-2)
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
            self._command(f"CREATE PROPERTY {CLAUSE_TYPE}.span_id STRING")  # the operative span (1:1; ADR-0025)
            # ADR-0048: multi-label classification, JSON-encoded [{function, confidence}] ranked primary-first.
            self._command(f"CREATE PROPERTY {CLAUSE_TYPE}.functions STRING")
        if PROPVALUE_TYPE not in types:  # shared, deduped (dimension,value) node
            self._command(f"CREATE VERTEX TYPE {PROPVALUE_TYPE}")
            self._command(f"CREATE PROPERTY {PROPVALUE_TYPE}.value_key STRING")
            self._command(f"CREATE PROPERTY {PROPVALUE_TYPE}.dimension STRING")
            self._command(f"CREATE PROPERTY {PROPVALUE_TYPE}.value STRING")
            self._command(f"CREATE PROPERTY {PROPVALUE_TYPE}.folio_iri STRING")
            self._command(f"CREATE PROPERTY {PROPVALUE_TYPE}.canonical_value STRING")  # KG-5a
        if PROPERTY_EDGE_TYPE not in types:  # legacy flat edge; kept for back-compat, superseded by typed
            self._command(f"CREATE EDGE TYPE {PROPERTY_EDGE_TYPE}")
        for typed_edge in TYPED_PROPERTY_EDGE_TYPES:  # KG-3: the typed property-edge layer (ADR-0033)
            if typed_edge not in types:
                self._command(f"CREATE EDGE TYPE {typed_edge}")
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

        if PARTY_TO_EDGE_TYPE not in types:  # KG-7 (ADR-0036): Entity(party) -> Contract unifying link
            self._command(f"CREATE EDGE TYPE {PARTY_TO_EDGE_TYPE}")
        if IS_EXCEPTION_TO_EDGE_TYPE not in types:  # ADR-0044: exception clause -> the Cap clause it excepts
            self._command(f"CREATE EDGE TYPE {IS_EXCEPTION_TO_EDGE_TYPE}")

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

    # --- Compliance module (CC-5, §13): the Requirement KG, in its OWN database (ragwright_compliance) ---

    def ensure_compliance_schema(self) -> None:
        """Create the compliance schema: the `Requirement` vertex type + a UNIQUE index on `requirement_id`.
        Additive + idempotent (create only what is absent, by introspection). Intended for a SEPARATE database
        (`ragwright_compliance`) so the contract KG stays clean; touches no existing type or identifier."""
        if REQUIREMENT_TYPE in self.type_names():
            return
        self._command(f"CREATE VERTEX TYPE {REQUIREMENT_TYPE}")
        for prop in ("requirement_id", "source", "citation", "deontic_type", "actor", "requirement_text",
                     "evidence_standard", "severity", "applicability_json", "confidence"):
            self._command(f"CREATE PROPERTY {REQUIREMENT_TYPE}.{prop} STRING")
        self._command(f"CREATE INDEX ON {REQUIREMENT_TYPE} (requirement_id) UNIQUE")

    def write_requirements(self, requirements: Iterable[Requirement]) -> int:
        """Upsert `Requirement` nodes by `requirement_id` (idempotent -- a re-ingest is a no-op on unchanged
        rules). `applicability_scope` is stored JSON-encoded ([[dimension, value], ...]); returns the count."""
        count = 0
        for req in requirements:
            scope = json.dumps([[c.dimension, c.value] for c in req.applicability_scope])
            self._command(
                f"UPDATE {REQUIREMENT_TYPE} SET"
                f" requirement_id = {_sql_str(req.requirement_id)},"
                f" source = {_sql_str(req.source)},"
                f" citation = {_sql_str(req.citation)},"
                f" deontic_type = {_sql_str(req.deontic_type.value)},"
                f" actor = {_sql_str(req.actor)},"
                f" requirement_text = {_sql_str(req.requirement_text)},"
                f" evidence_standard = {_sql_str(req.evidence_standard or '')},"
                f" severity = {_sql_str(req.severity.value if req.severity else '')},"
                f" applicability_json = {_sql_str(scope)},"
                f" confidence = {_sql_str(req.confidence.value)}"
                f" UPSERT WHERE requirement_id = {_sql_str(req.requirement_id)}"
            )
            count += 1
        return count

    def all_requirements(self, sources: Optional[Iterable[str]] = None) -> list[dict]:
        """Stored `Requirement` rows (CC-6 loads these to match a claim's scope against applicability).

        `sources=None` returns every row (store-wide, unchanged). Issue 0007: when a list of policy `source`s is
        given, the filter is pushed into the QUERY (`WHERE source IN [...]`) so a store holding thousands of rows
        across many policies/tenants never fetches the ones outside the scope -- scale-ready, not an in-memory
        filter. An empty scope (`sources=[]`) returns `[]` without a query (scope-to-nothing; also avoids an
        invalid `IN []`)."""
        select = (
            f"SELECT requirement_id, source, citation, deontic_type, actor, requirement_text,"
            f" evidence_standard, severity, applicability_json, confidence FROM {REQUIREMENT_TYPE}")
        if sources is None:
            return self._query(select)
        sources = list(sources)
        if not sources:
            return []
        return self._query(f"{select} WHERE source IN {_str_array(sources)}")

    def requirement_sources(self) -> set[str]:
        """Issue 0007: the DISTINCT set of policy `source`s present in the Requirement KG -- powers unknown-source
        validation (naming a policy that does not exist) WITHOUT loading any requirement rows. The Requirement type
        may not exist yet on a fresh DB -> empty set."""
        if REQUIREMENT_TYPE not in self.type_names():
            return set()
        rows = self._query(f"SELECT DISTINCT(source) AS s FROM {REQUIREMENT_TYPE}")
        return {r["s"] for r in rows if r.get("s")}

    def ingested_citations(self, source: str) -> set[str]:
        """COMP-ASYNC-1 resume (PROD-2 #2): the set of `citation`s that ALREADY have >=1 `Requirement` for `source`
        -- the compliance analogue of a present `Contract` node. A section in this set was successfully ingested
        (a FAILED or genuinely-empty section wrote 0 requirements, so it is absent and correctly re-runs). The
        Requirement type may not exist yet on a fresh DB -> empty set."""
        if REQUIREMENT_TYPE not in self.type_names():
            return set()
        rows = self._query(
            f"SELECT DISTINCT(citation) AS c FROM {REQUIREMENT_TYPE} WHERE source = {_sql_str(source)}")
        return {r["c"] for r in rows if r.get("c")}

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

    def span_properties(self, span_ids: list[str]) -> dict[str, set[tuple[str, str]]]:
        """The typed property assertions on each span, joined via the ADR-0025 `span_id` key that the clause
        KG persists on every property edge (`edge.span_id == Span.span_id`; SPAN-CLAUSE-RERANK). This is the
        clause<->span link the retrieval rerank needs: a span retrieved from the BGE index gets its clause's
        typed (dimension, value) constraints here. Batched over `span_ids`; returns {span_id: {(dimension,
        value)}}. Queries each typed edge type once with an IN filter (ArcadeDB has no shared edge base), so
        round-trips are bounded by the edge-type count, not the pool size. `value` is the target
        PropertyValue's value (`inV().value`)."""
        out: dict[str, set[tuple[str, str]]] = {s: set() for s in span_ids}
        if not span_ids:
            return out
        id_list = "[" + ",".join(_sql_str(s) for s in span_ids) + "]"
        for edge_type in sorted(set(_TYPED_DIMENSION_EDGE.values())):
            rows = self._query(
                f"SELECT span_id, dimension, inV().value AS value FROM {edge_type} WHERE span_id IN {id_list}")
            for r in rows:
                sid, dim, val = r.get("span_id"), r.get("dimension"), r.get("value")
                if sid in out and dim and val is not None:
                    out[sid].add((str(dim), str(val)))
        return out

    def span_texts(self, span_ids: list[str]) -> dict[str, str]:
        """The operative-span text for each span_id (batched), for citing a retrieved span. {span_id: text}."""
        if not span_ids:
            return {}
        id_list = "[" + ",".join(_sql_str(s) for s in span_ids) + "]"
        rows = self._query(f"SELECT span_id, text FROM {SPAN_TYPE} WHERE span_id IN {id_list}")
        return {r["span_id"]: r.get("text", "") for r in rows}

    def mark_span_properties_ambiguous(self, span_ids: list[str]) -> int:
        """ADR-0048 Phase A mark-stale: set `confidence = AMBIGUOUS` on every typed property edge of these spans.
        A clause whose PRIMARY function flipped had its properties extracted for the OLD function, so they are
        stale until Phase B re-extraction -- downgraded (kept but flagged) exactly as the ADR-0040 judges do, so
        the soft-boost down-weights them meanwhile. Keyed by the ADR-0025 `edge.span_id`. Idempotent (skips
        already-AMBIGUOUS). Returns the number of edges downgraded (best-effort from the driver's row count)."""
        if not span_ids:
            return 0
        total = 0
        for stmt in _stale_property_statements(span_ids):
            res = self._command(stmt)
            if isinstance(res, list):
                for row in res:
                    if isinstance(row, dict) and "count" in row:
                        total += int(row["count"])
        return total

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

    def all_contracts(self) -> list[dict]:
        """KG-7: every contract id (the set a PARTY_TO edge's Entity provenance must land in)."""
        return self._query(f"SELECT contract_id FROM {CONTRACT_TYPE}")

    def all_entities(self) -> list[dict]:
        """KG-7: every party `Entity`'s node key, name, and `chunk_id` (its extraction-provenance contract)."""
        return self._query(f"SELECT entity_id, name, chunk_id FROM {ENTITY_TYPE}")

    def write_party_contract_links(self, links: list) -> None:
        """KG-7 (ADR-0036): write the `PARTY_TO` edges (Entity -> Contract). Idempotent: clears the existing
        PARTY_TO layer first, so re-linking over the populated graph is safe and re-derivable. One transaction."""
        statements = [f"DELETE FROM {PARTY_TO_EDGE_TYPE} UNSAFE"] if PARTY_TO_EDGE_TYPE in self.type_names() else []
        for link in links:
            statements.append(
                f"CREATE EDGE {PARTY_TO_EDGE_TYPE}"
                f" FROM (SELECT FROM {ENTITY_TYPE} WHERE entity_id = {_sql_str(link.entity_id)})"
                f" TO (SELECT FROM {CONTRACT_TYPE} WHERE contract_id = {_sql_str(link.contract_id)})"
                f" SET party_name = {_sql_str(link.party_name)}"
            )
        if statements:
            self._db.execute_transaction(statements)

    # --- ADR-0044: the IS_EXCEPTION_TO derived carve-out relationship (exception clause -> Cap clause) ------

    def clause_positions(self, functions: list[str]) -> list[dict]:
        """Clauses of the given functions with their operative-span DOCUMENT offsets (via the clause-level
        span_id, ADR-0042), for proximity-based exception linking. Rows: {clause_id, function, contract_id,
        doc_start, doc_end}. A clause with no resolvable span (legacy/unbackfilled) is skipped."""
        if not functions:
            return []
        fn_list = "[" + ",".join(_sql_str(f) for f in functions) + "]"
        clauses = self._query(
            f"SELECT clause_id, function, span_id FROM {CLAUSE_TYPE} WHERE function IN {fn_list}")
        span_ids = [c["span_id"] for c in clauses if c.get("span_id")]
        if not span_ids:
            return []
        id_list = "[" + ",".join(_sql_str(s) for s in span_ids) + "]"
        spans = self._query(
            f"SELECT span_id, doc_start, doc_end, contract_id FROM {SPAN_TYPE} WHERE span_id IN {id_list}")
        by_span = {s["span_id"]: s for s in spans}
        out: list[dict] = []
        for c in clauses:
            s = by_span.get(c.get("span_id"))
            if s is None:
                continue
            out.append({"clause_id": c["clause_id"], "function": c["function"],
                        "contract_id": s.get("contract_id"), "doc_start": s.get("doc_start"),
                        "doc_end": s.get("doc_end")})
        return out

    def write_clause_exception_links(self, links: list) -> None:
        """ADR-0044: write the `IsExceptionTo` edges (exception/Uncapped clause -> the Cap clause it excepts).
        Idempotent: clears the existing IsExceptionTo layer first, so re-linking is safe and re-derivable. The
        edge carries the INFERRED confidence (a derived, reasoned link, FR-S.4). One transaction."""
        statements = (
            [f"DELETE FROM {IS_EXCEPTION_TO_EDGE_TYPE} UNSAFE"]
            if IS_EXCEPTION_TO_EDGE_TYPE in self.type_names() else [])
        for link in links:
            statements.append(
                f"CREATE EDGE {IS_EXCEPTION_TO_EDGE_TYPE}"
                f" FROM (SELECT FROM {CLAUSE_TYPE} WHERE clause_id = {_sql_str(link.exception_clause_id)})"
                f" TO (SELECT FROM {CLAUSE_TYPE} WHERE clause_id = {_sql_str(link.cap_clause_id)})"
                f" SET confidence = {_sql_str(link.confidence.value)}"
            )
        if statements:
            self._db.execute_transaction(statements)

    def exceptions_of_clause(self, cap_clause_id: str) -> list[dict]:
        """The exception/carve-out clauses linked to a Cap clause (`IsExceptionTo` in-edges, ADR-0044). For the
        query side: serving a cap clause pulls its INFERRED carve-outs. Rows: {clause_id, function, span_id}."""
        return self._query(
            f"SELECT clause_id, function, span_id FROM ("
            f"SELECT expand(in('{IS_EXCEPTION_TO_EDGE_TYPE}')) FROM {CLAUSE_TYPE} "
            f"WHERE clause_id = {_sql_str(cap_clause_id)})")

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

    # --- KG-3 (ADR-0033): the TYPED unified clause KG (replaces the flat HasProperty write path) ---------

    def write_clause_kg(self, record: ClausePropertyRecord) -> None:
        """Write the clause + its TYPED property edges + shared value nodes in ONE transaction (FR-S.1).
        The KG-3 counterpart of `write_property_graph`: one sanctioned typed edge per assertion
        (HAS_*/EXCEPTS/COVERS/PROHIBITS/REQUIRES/CAPS/BOUNDED_BY/GOVERNED_BY), each grounded with a predicate
        IRI (ODRL for the deontic edges) and carrying provenance (FR-S.4/FR-Q.6). Idempotent by the same
        content-hash gate: `clause_id` embeds the content hash, so a committed clause means identical content
        -> identical assertions; we skip, and the typed `CREATE EDGE` therefore runs at most once per clause
        (no duplicates). Value-node identity (`value_key`) is preserved from the flat graph."""
        cid = _sql_str(record.clause_id)
        if self._query(f"SELECT clause_id FROM {CLAUSE_TYPE} WHERE clause_id = {cid} LIMIT 1"):
            return  # content-hash gate: a committed clause_id -> identical typed assertions
        self._db.execute_transaction(_clause_kg_statements(record))

    def clause_kg_counts(self) -> dict[str, int]:
        """Counts for the typed KG (introspection/tests): clauses, shared value nodes, and the total of the
        typed property edges across all typed edge types."""
        clauses = self._query(f"SELECT count(*) AS n FROM {CLAUSE_TYPE}")
        values = self._query(f"SELECT count(*) AS n FROM {PROPVALUE_TYPE}")
        typed = 0
        present = self.type_names()  # a DB populated before a schema extension lacks the newer edge types
        for edge_type in TYPED_PROPERTY_EDGE_TYPES:
            if edge_type not in present:
                continue
            rows = self._query(f"SELECT count(*) AS n FROM {edge_type}")
            typed += int(rows[0]["n"]) if rows else 0
        return {
            "clauses": int(clauses[0]["n"]) if clauses else 0,
            "property_values": int(values[0]["n"]) if values else 0,
            "typed_edges": typed,
        }

    def clause_typed_edges(self, clause_id: str) -> list[dict]:
        """The clause's typed property edges: the edge TYPE, dimension, value, predicate IRI, and provenance
        (confidence, span_id) -- the readback for tests and the shape the Leg-A/B scoped queries build on."""
        q = (
            "MATCH {type: " + CLAUSE_TYPE + ", as: c, where: (clause_id = " + _sql_str(clause_id) + ")}"
            ".outE(){as: e}.inV(){as: v}"
            " RETURN e.@type AS edge_type, e.predicate_iri AS predicate_iri, v.dimension AS dimension,"
            " v.value AS value, v.folio_iri AS folio_iri, e.confidence AS confidence, e.span_id AS span_id"
        )
        return self._query(q)

    def clear_clause_kg(self) -> None:
        """Delete the typed clause KG (all typed edges + the legacy flat edge + Clause + PropertyValue),
        leaving the span index intact -- the KG-3 counterpart of `clear_property_graph` for a clean
        re-extraction into the typed schema. Edges first (UNSAFE bypasses the edge-safety check)."""
        present = self.type_names()  # skip edge types a pre-extension DB never created
        for edge_type in (*TYPED_PROPERTY_EDGE_TYPES, PROPERTY_EDGE_TYPE):
            if edge_type in present:
                self._command(f"DELETE FROM {edge_type} UNSAFE")
        self._command(f"DELETE FROM {CLAUSE_TYPE}")
        self._command(f"DELETE FROM {PROPVALUE_TYPE}")

    def patch_canonical_jurisdictions(self) -> dict[str, int]:
        """KG-5a: additively canonicalize the `jurisdiction` value nodes -- write a `canonical_value` on each
        (surface `value` untouched, kept for citation), deterministically (no LLM, no re-extraction). Fixes
        the retrieval-match loss from surface variants (England / England and Wales / English law). Idempotent.
        Returns {seen, canonicalized}."""
        from rag_wright.contracts.jurisdiction import canonicalize_jurisdiction

        if "canonical_value" not in self.property_names(PROPVALUE_TYPE):
            self._command(f"CREATE PROPERTY {PROPVALUE_TYPE}.canonical_value STRING")
        rows = self._query(
            f"SELECT value_key, value FROM {PROPVALUE_TYPE} WHERE dimension = 'jurisdiction'"
        )
        n = 0
        for r in rows:
            canon = canonicalize_jurisdiction(r["value"])
            if canon:
                self._command(
                    f"UPDATE {PROPVALUE_TYPE} SET canonical_value = {_sql_str(canon)} "
                    f"WHERE value_key = {_sql_str(r['value_key'])}"
                )
                n += 1
        return {"seen": len(rows), "canonicalized": n}

    # --- KG-4 (Leg A): intra-contract scoped queries over the typed KG ------------------------------
    # A clause_id is `<contract_id>:<index>:<hash>` (FR-S.2), and contract_id is delimiter-safe, so a
    # contract's clauses are exactly the half-open key range [`<cid>:`, `<cid>;`) (';' = ':'+1). This is an
    # exact prefix scan -- no LIKE (whose `_` would wildcard the underscores in CUAD contract ids).

    def _contract_bounds(self, contract_id: str) -> tuple[str, str]:
        return _sql_str(contract_id + ":"), _sql_str(contract_id + ";")

    def clauses_in_contract(self, contract_id: str) -> list[dict]:
        """Every clause in one contract (Leg-A scope): clause_id, function, folio_iri -- including clauses
        with no typed properties (still queryable by type)."""
        lo, hi = self._contract_bounds(contract_id)
        return self._query(
            f"SELECT clause_id, function, folio_iri, span_id FROM {CLAUSE_TYPE} "
            f"WHERE clause_id >= {lo} AND clause_id < {hi} ORDER BY clause_id"
        )

    def contract_clause_kg(self, contract_id: str) -> list[dict]:
        """The per-contract typed subgraph: one row per typed edge (clause -> value), carrying the clause
        function, the edge type + dimension + value + predicate IRI, and the provenance (confidence, span_id).
        The shape Leg-A aggregation / disambiguation / citation build on."""
        lo, hi = self._contract_bounds(contract_id)
        # Only typed-PROPERTY edges (clause -> value), which carry a `dimension`. A clause can also have
        # clause->clause edges with no dimension (IsExceptionTo, ADR-0044); the `dimension IS NOT NULL` guard
        # excludes those so they never surface as null-dimension "property" rows.
        q = (
            "MATCH {type: " + CLAUSE_TYPE + ", as: c, where: (clause_id >= " + lo
            + " AND clause_id < " + hi + ")}.outE(){as: e, where: (dimension IS NOT NULL)}.inV(){as: v}"
            " RETURN c.clause_id AS clause_id, c.function AS function, e.@type AS edge_type,"
            " e.dimension AS dimension, v.value AS value, v.folio_iri AS folio_iri,"
            " e.predicate_iri AS predicate_iri, e.confidence AS confidence, e.span_id AS span_id"
        )
        return self._query(q)

    def clauses_with_property(self, contract_id: str, dimension: str, value: str) -> list[dict]:
        """Disambiguation: the clauses in one contract that assert (dimension, value) -- e.g. the *mutual*
        cap clause, or every clause that covers *fraud*. Returns clause_id + function + the edge provenance."""
        lo, hi = self._contract_bounds(contract_id)
        q = (
            "MATCH {type: " + CLAUSE_TYPE + ", as: c, where: (clause_id >= " + lo
            + " AND clause_id < " + hi + ")}"
            ".outE(){as: e, where: (dimension = " + _sql_str(dimension) + ")}"
            ".inV(){as: v, where: (value = " + _sql_str(value) + ")}"
            " RETURN c.clause_id AS clause_id, c.function AS function, e.@type AS edge_type,"
            " e.confidence AS confidence, e.span_id AS span_id"
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
