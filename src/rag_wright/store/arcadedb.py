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
import random
import time
from typing import Any, Iterable, Optional

from arcadedb_python import DatabaseDao, SyncClient

from rag_wright.contracts.chunk import BGE_M3_DENSE_DIM, ChunkRecord, MetadataValue
from rag_wright.corpus.canonicalize import normalize_entity_name  # issue 0030: name -> entity clustering key
from rag_wright.ontology.pack_schema import load_kg_schema  # ADR-0067 P5b: a pack's declared KG schema (generic)
from rag_wright.contracts.span import SpanRecord
from rag_wright.store.seam import NOT_NULL, GraphEdge, GraphNode

CHUNK_TYPE = "Chunk"
ENTITY_TYPE = "Entity"
REL_EDGE_TYPE = "Relationship"  # entity -> entity relationship edge (the graph's primary content)
MENTIONS_EDGE_TYPE = "Mentions"  # chunk -> entity provenance edge (FR-S.1: chunk and entities connect)

# Candidates fetched per leg before fusion. RRF reorders within this pool, so it is set well above a
# typical final `k` to give fusion (and any metadata filter) room to work; the fused list is then
# cut to `k`. Tuned at GATE-2 against the golden set if recall calls for it.
DEFAULT_CANDIDATE_POOL = 100
SCOPED_CANDIDATE_POOL = 1000  # issue 0031: a larger KNN pool when a `documents` scope filters AFTER the vector
#   legs, so a small workspace does not under-fill k (the vector functions do not pre-filter; ADR-0008)

SPAN_TYPE = "Span"  # FR-R (ADR-0025): the operative-span hybrid index; dense+sparse over the span text
DOCUMENT_TYPE = "Document"  # ING-4b (ADR-0124): one node per ingested document, incl. embedded children
EMBEDDED_IN_EDGE_TYPE = "EmbeddedIn"  # ING-4b: child Document -> parent Document (position as provenance)
ATTACHED_TO_EDGE_TYPE = "AttachedTo"  # ING-4b: child Document -> the record-row Span it belongs to (+ confidence)

# Expected index names follow ArcadeDB's `Type[prop]` / `Type[p1,p2]` convention.
_DENSE_INDEX = f"{CHUNK_TYPE}[dense]"
_SPARSE_INDEX = f"{CHUNK_TYPE}[sparse_indices,sparse_weights]"
_CHUNK_ID_INDEX = f"{CHUNK_TYPE}[chunk_id]"
_ENTITY_ID_INDEX = f"{ENTITY_TYPE}[entity_id]"
_SPAN_ID_INDEX = f"{SPAN_TYPE}[span_id]"
# ING-8d: the pre-ING-8d Span field names -> the generic ones (`migrate_span_fields`; `ensure_schema` refuses the old)
_SPAN_FIELD_RENAMES = {"contract_id": "document_id", "function": "primary_tag", "functions": "tags"}
_DOCUMENT_ID_INDEX = f"{DOCUMENT_TYPE}[doc_id]"
_SPAN_DENSE_INDEX = f"{SPAN_TYPE}[dense]"
_SPAN_SPARSE_INDEX = f"{SPAN_TYPE}[sparse_indices,sparse_weights]"
# ADR-0067 P5b: the domain vertex UNIQUE id indexes (Clause/PropertyValue/Contract) are pack-declared
# (cbr:uniqueIndexOn) and built by the generic ensure_schema loop, not hardcoded here.


# ArcadeDB answers concurrent writes to the same page with a `ConcurrentModificationException` and asks the client to
# retry; the failed command or transaction is rolled back whole, so a bounded retry with jittered backoff is safe
# (found by the ING-5 doc audit: unretried, concurrent ingestion silently lost spans and records).
_CONFLICT_RETRIES = 10


def _is_retryable_conflict(exc: BaseException) -> bool:
    msg = str(exc)
    return "ConcurrentModificationException" in msg or "Concurrent modification on page" in msg


def _retry_on_conflict(fn, *args, **kwargs):
    for attempt in range(_CONFLICT_RETRIES):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - only the retryable conflict is retried; anything else re-raises
            if attempt == _CONFLICT_RETRIES - 1 or not _is_retryable_conflict(exc):
                raise
            time.sleep(min(0.5, 0.01 * 2 ** attempt) + random.uniform(0, 0.01))


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


def _kg_sql_value(value: object) -> str:
    """Serialize a scalar for `kg_read` (DD-1a): bool/int/float native, everything else a quoted string."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    return _sql_str(str(value))


def _kg_sql_array(values: list) -> str:
    return "[" + ",".join(_kg_sql_value(v) for v in values) + "]"


def _kg_sql(value: object) -> str:
    """Type-driven serialization for `kg_write` EDGE props + undeclared fields: None->null, scalars native/quoted,
    list/tuple -> a nested array literal."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_kg_sql(v) for v in value) + "]"
    return _sql_str(str(value))


def _kg_where_terms(where: Optional[dict[str, object]],
                    key_range: Optional[tuple[str, object, object]] = None) -> Optional[list[str]]:
    """The shared KG filter grammar (`kg_read` key ranges, `kg_edges`, `kg_count` / `kg_delete` / `kg_update`): a
    scalar is equality, a list is membership, `NOT_NULL` is presence; `key_range=(field, lo, hi)` adds
    `field >= lo AND field < hi`. Returns the AND-ed terms, or None when a membership list is EMPTY (scope-to-nothing:
    the caller returns without a statement)."""
    out: list[str] = []
    for field, value in (where or {}).items():
        if value is NOT_NULL:
            out.append(f"{field} IS NOT NULL")
        elif isinstance(value, (list, tuple, set)):
            vals = list(value)
            if not vals:
                return None
            out.append(f"{field} IN {_kg_sql_array(vals)}")
        else:
            out.append(f"{field} = {_kg_sql_value(value)}")
    if key_range is not None:
        f, lo, hi = key_range
        out += [f"{f} >= {_kg_sql_value(lo)}", f"{f} < {_kg_sql_value(hi)}"]
    return out


def _kg_changed_count(result: Any) -> int:
    """The row count an ArcadeDB UPDATE / DELETE reports (`[{"count": n}]`); 0 when it reports none."""
    if isinstance(result, list) and result and isinstance(result[0], dict):
        return int(result[0].get("count") or 0)
    return 0


def _kg_encode(value: object, declared_type: Optional[str]) -> str:
    """Encode a `kg_write` NODE prop by its PACK-DECLARED storage type (DD-1b): the declared type is what
    disambiguates a list stored as a native array (`ARRAY_OF_*`) from one stored as a JSON string (`STRING`) --
    e.g. `pages` (array) vs `bbox` (JSON string). None->null; an undeclared field falls back to type-driven."""
    if value is None:
        return "null"
    dt = (declared_type or "").upper()
    if dt == "STRING":
        return _sql_str(value if isinstance(value, str) else json.dumps(value))
    if dt in ("INTEGER", "LONG", "SHORT", "BYTE"):
        return str(int(value))
    if dt in ("FLOAT", "DOUBLE", "DECIMAL"):
        return repr(float(value))
    if dt == "BOOLEAN":
        return "true" if value else "false"
    if dt == "ARRAY_OF_INTEGERS":
        return "[" + ",".join(str(int(x)) for x in value) + "]"
    if dt == "ARRAY_OF_FLOATS":
        return _float_array(value)
    if dt == "ARRAY_OF_STRINGS":
        return _str_array(value)
    return _kg_sql(value)


def _doc_id_of(chunk_id: str) -> str:
    """The source-document id embedded in a chunk/span/clause id (issue 0031). The id scheme is
    `<source_doc_id>:<index>:<hash>` and `source_doc_id` is delimiter-safe (no ':', enforced by `ChunkId`),
    so the document id is exactly the prefix before the first ':'. Empty in -> empty out."""
    return (chunk_id or "").split(":", 1)[0]


def _edge_provenance_assignments(chunk_id: str) -> str:
    """The `SET` fragment writing an edge's provenance `chunk_id` and its DERIVED `source_doc_id` as ONE
    matched pair from a SINGLE source (issue 0031 ask #2). `source_doc_id` is a denormalization of `chunk_id`
    for index-backed scoping; keeping the derivation in this one place is what makes the two impossible to
    drift. A drifted pair (a `source_doc_id` disagreeing with its `chunk_id`) is an INVISIBLE cross-matter
    leak -- the scoping filter would silently admit an out-of-scope edge -- so every edge writer MUST emit the
    pair through here, never assign the two fields independently."""
    return f"chunk_id = {_sql_str(chunk_id)}, source_doc_id = {_sql_str(_doc_id_of(chunk_id))}"


def _sql_literal(value: MetadataValue) -> str:
    """A SQL literal for a filterable metadata scalar (bool checked before int: `bool` subclasses `int`)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return _sql_str(str(value))


class ArcadeDBStore:
    """The default store: schema management over a single ArcadeDB database."""

    def __init__(self, client: SyncClient, database: str, *, pack_ttl: str | None = None) -> None:
        self._client = client
        self._database = database
        self._db = DatabaseDao(client, database)
        # AC-journey / ING-8a: the DOMAIN pack `.ttl` whose KG vertex/edge types `ensure_schema` creates and whose
        # property storage types `kg_write` encodes by. `None` = NO domain pack (the neutral engine schema); a domain
        # points this at its OWN pack ("config + .ttl", no engine edit), or ensures it on use (`ensure_pack_schema`).
        self._pack_ttl = pack_ttl

    @classmethod
    def from_env(cls, *, database: str | None = None, reset: bool = False,
                 pack_ttl: str | None = None) -> "ArcadeDBStore":
        """Build a store from `ARCADEDB_*` env, creating the database if absent.

        `database` overrides `ARCADEDB_DATABASE` (used to point tests at a scratch database).
        `reset=True` drops and recreates the database first, for a clean-slate test.
        `pack_ttl` selects the domain pack schema (None = the neutral engine schema, no domain pack).
        """
        return cls.from_config(
            os.environ["ARCADEDB_HOST"], os.environ["ARCADEDB_PORT"],
            os.environ["ARCADEDB_USER"], os.environ["ARCADEDB_PASSWORD"],
            database=database or os.environ["ARCADEDB_DATABASE"],
            protocol=os.getenv("ARCADEDB_PROTOCOL", "http"),  # `https` for the Modal-hosted KG (EC-2)
            reset=reset, pack_ttl=pack_ttl)

    @classmethod
    def from_config(cls, host: str, port: str, user: str, password: str, *, database: str,
                    protocol: str = "http", reset: bool = False,
                    pack_ttl: str | None = None) -> "ArcadeDBStore":
        """Build a store from EXPLICIT connection params (EP-API-1: the de-env'd twin of `from_env`, so engine
        config flows as data, not `os.environ`). Creates the database if absent; `reset=True` drops + recreates it.
        `pack_ttl` selects the domain pack schema (None = the neutral engine schema, no domain pack)."""
        client = SyncClient(host, port, protocol=protocol, username=user, password=password)
        if reset and DatabaseDao.exists(client, database):
            DatabaseDao.delete(client, database)
        if not DatabaseDao.exists(client, database):
            DatabaseDao.create(client, database)
        return cls(client, database, pack_ttl=pack_ttl)

    # --- seam surface ---------------------------------------------------------------------------

    def ensure_schema(self) -> None:
        """Create the chunk-record and graph-node types and the hybrid indexes, idempotently. ING-8d: refuses a
        `Span` type that still declares the pre-ING-8d field names, so an unmigrated KG fails loudly instead of
        returning silently-empty filters (migrate it with `migrate_span_fields`)."""
        types = self.type_names()
        if SPAN_TYPE in types and (stale := sorted(set(_SPAN_FIELD_RENAMES) & self.property_names(SPAN_TYPE))):
            raise RuntimeError(
                f"this database's {SPAN_TYPE} type still has the pre-ING-8d fields {stale}; migrate it first: "
                f"`uv run python scripts/migrate_span_fields.py {self._database}` (ArcadeDBStore.migrate_span_fields)")
        if CHUNK_TYPE not in types:
            self._command(f"CREATE VERTEX TYPE {CHUNK_TYPE}")
            self._command(f"CREATE PROPERTY {CHUNK_TYPE}.chunk_id STRING")
            self._command(f"CREATE PROPERTY {CHUNK_TYPE}.source_doc_id STRING")
            self._command(f"CREATE PROPERTY {CHUNK_TYPE}.dense ARRAY_OF_FLOATS")
            self._command(f"CREATE PROPERTY {CHUNK_TYPE}.sparse_indices ARRAY_OF_INTEGERS")
            self._command(f"CREATE PROPERTY {CHUNK_TYPE}.sparse_weights ARRAY_OF_FLOATS")
        if ENTITY_TYPE not in types:
            self._command(f"CREATE VERTEX TYPE {ENTITY_TYPE}")
            self._command(f"CREATE PROPERTY {ENTITY_TYPE}.entity_id STRING")  # node key (canonical id or surrogate)
            self._command(f"CREATE PROPERTY {ENTITY_TYPE}.chunk_id STRING")
            self._command(f"CREATE PROPERTY {ENTITY_TYPE}.canonical_id STRING")  # ADR-0067 P5c: the resolver's canonical id, or '' if unlinked
            self._command(f"CREATE PROPERTY {ENTITY_TYPE}.name STRING")
            self._command(f"CREATE PROPERTY {ENTITY_TYPE}.entity_type STRING")
            self._command(f"CREATE PROPERTY {ENTITY_TYPE}.confidence STRING")
        if REL_EDGE_TYPE not in types:
            self._command(f"CREATE EDGE TYPE {REL_EDGE_TYPE}")
            # issue 0031: the source-document id (derived from the edge's provenance chunk_id) so a graph
            # traversal can be scoped to a workspace's documents (`WHERE source_doc_id IN [...]`).
            self._command(f"CREATE PROPERTY {REL_EDGE_TYPE}.source_doc_id STRING")
        if MENTIONS_EDGE_TYPE not in types:
            self._command(f"CREATE EDGE TYPE {MENTIONS_EDGE_TYPE}")
        if SPAN_TYPE not in types:  # FR-R (ADR-0025): operative-span hybrid index
            self._command(f"CREATE VERTEX TYPE {SPAN_TYPE}")
            self._command(f"CREATE PROPERTY {SPAN_TYPE}.span_id STRING")
            self._command(f"CREATE PROPERTY {SPAN_TYPE}.parent_chunk_id STRING")
            self._command(f"CREATE PROPERTY {SPAN_TYPE}.span_index INTEGER")
            self._command(f"CREATE PROPERTY {SPAN_TYPE}.text STRING")
            self._command(f"CREATE PROPERTY {SPAN_TYPE}.primary_tag STRING")  # the span tagger's PRIMARY tag (ING-8d)
            self._command(f"CREATE PROPERTY {SPAN_TYPE}.tags STRING")  # ADR-0114: top-k soft tags, JSON list (ING-8d)
            self._command(f"CREATE PROPERTY {SPAN_TYPE}.dense ARRAY_OF_FLOATS")
            self._command(f"CREATE PROPERTY {SPAN_TYPE}.sparse_indices ARRAY_OF_INTEGERS")
            self._command(f"CREATE PROPERTY {SPAN_TYPE}.sparse_weights ARRAY_OF_FLOATS")
            self._command(f"CREATE PROPERTY {SPAN_TYPE}.document_id STRING")  # within-document filter (ING-8d)
            self._command(f"CREATE PROPERTY {SPAN_TYPE}.doc_start INTEGER")  # CU-B2: doc-absolute char offset
            self._command(f"CREATE PROPERTY {SPAN_TYPE}.doc_end INTEGER")  # CU-B2: exclusive (citation)
            self._command(f"CREATE PROPERTY {SPAN_TYPE}.pages ARRAY_OF_INTEGERS")  # issue 0032: source page(s)
            self._command(f"CREATE PROPERTY {SPAN_TYPE}.bbox STRING")  # issue 0032: best-effort [l,t,r,b] JSON
        if DOCUMENT_TYPE not in types:  # ING-4b (ADR-0124): the generic document node + its link edges
            self._command(f"CREATE VERTEX TYPE {DOCUMENT_TYPE}")
            for prop in ("doc_id", "parent_doc_id", "filename", "media_type", "sha256"):
                self._command(f"CREATE PROPERTY {DOCUMENT_TYPE}.{prop} STRING")
        for edge in (EMBEDDED_IN_EDGE_TYPE, ATTACHED_TO_EDGE_TYPE):
            if edge not in types:
                self._command(f"CREATE EDGE TYPE {edge}")
        indexes = self.index_names()
        if _CHUNK_ID_INDEX not in indexes:
            self._command(f"CREATE INDEX ON {CHUNK_TYPE} (chunk_id) UNIQUE")
        if _DOCUMENT_ID_INDEX not in indexes:
            self._command(f"CREATE INDEX ON {DOCUMENT_TYPE} (doc_id) UNIQUE")
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
        # ING-8a: the engine default is domain-NEUTRAL -- a domain's types come only from a CONFIGURED pack
        if getattr(self, "_pack_ttl", None):
            self.ensure_pack_schema(self._pack_ttl)

    def schema_packs(self) -> list[str]:
        """ING-8a: the pack `.ttl`s this store's schema includes -- the configured pack (if any) plus every pack
        ensured on use (`ensure_pack_schema`). Empty for the neutral default."""
        configured = [self._pack_ttl] if getattr(self, "_pack_ttl", None) else []
        return list(dict.fromkeys(configured + list(getattr(self, "_ensured_packs", []))))

    def ensure_edge_types(self, names, *, types: set[str] | None = None) -> None:
        """ING-8b: create these edge types if absent (a pack's own edge vocabulary)."""
        types = self.type_names() if types is None else types
        for edge in names:
            if edge not in types:
                self._command(f"CREATE EDGE TYPE {edge}")

    def ensure_pack_schema(self, pack_ttl: str) -> None:
        """ING-8a/8b (ADR-0067 P5b): create a domain PACK's declared schema from its own `.ttl`, idempotently -- the
        vertex types + their unique id indexes and the structural edges (`ontology.pack_schema.load_kg_schema`).
        Called by `ensure_schema` for a configured pack, and by a pack on use (the reference contract pack does,
        then adds its own typed property edges via `ensure_edge_types`)."""
        vertex_types, structural_edges = load_kg_schema(pack_ttl)
        self._ensured_packs = [*getattr(self, "_ensured_packs", []), pack_ttl]
        self._prop_types_cache = None  # re-derive the property encodings with this pack included
        types = self.type_names()
        for vt in vertex_types:
            if vt.name not in types:
                self._command(f"CREATE VERTEX TYPE {vt.name}")
                for pname, ptype in vt.properties:
                    self._command(f"CREATE PROPERTY {vt.name}.{pname} {ptype}")
        self.ensure_edge_types(sorted(structural_edges), types=types)
        indexes = self.index_names()
        for vt in vertex_types:
            if vt.unique_index and f"{vt.name}[{vt.unique_index}]" not in indexes:
                self._command(f"CREATE INDEX ON {vt.name} ({vt.unique_index}) UNIQUE")

    def type_names(self) -> set[str]:
        return {row["name"] for row in self._query("SELECT name FROM schema:types")}

    def property_names(self, type_name: str) -> set[str]:
        for row in self._query("SELECT name, properties FROM schema:types"):
            if row.get("name") == type_name:
                return {prop["name"] for prop in row.get("properties", [])}
        return set()

    def migrate_span_fields(self, *, batch: int = 5000, progress: Any = None) -> int:
        """ING-8d: move a pre-ING-8d `Span` type to the generic field names (`contract_id` -> `document_id`,
        `function` -> `primary_tag`, `functions` -> `tags`), in batches, then drop the old properties. Idempotent:
        returns the number of records moved (0 when already migrated). The legacy `parent_okf_path` values are left
        in place (no longer declared by the engine). `progress(done, total)` is called after each batch."""
        if SPAN_TYPE not in self.type_names():
            return 0
        props = self.property_names(SPAN_TYPE)
        for old, new in _SPAN_FIELD_RENAMES.items():
            if new not in props:
                self._command(f"CREATE PROPERTY {SPAN_TYPE}.{new} STRING")
        pending = " OR ".join(f"{old} IS NOT NULL" for old in _SPAN_FIELD_RENAMES)
        total = self._query(f"SELECT count(*) AS n FROM {SPAN_TYPE} WHERE {pending}")[0]["n"]
        sets = ", ".join(f"{new} = {old}" for old, new in _SPAN_FIELD_RENAMES.items())
        removes = ", ".join(_SPAN_FIELD_RENAMES)
        done = 0
        while done < total:
            self._command(f"UPDATE {SPAN_TYPE} SET {sets} REMOVE {removes} WHERE {pending} LIMIT {int(batch)}")
            left = self._query(f"SELECT count(*) AS n FROM {SPAN_TYPE} WHERE {pending}")[0]["n"]
            if total - left <= done:
                raise RuntimeError(f"span migration made no progress at {done}/{total}")
            done = total - left
            if progress is not None:
                progress(done, total)
        for old in sorted(set(_SPAN_FIELD_RENAMES) & self.property_names(SPAN_TYPE)):
            self._command(f"DROP PROPERTY {SPAN_TYPE}.{old}")
        return total

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

    # --- generic typed-node read (DD-1a, ADR-0117): the backend-agnostic primitive the domain store
    # extensions delegate to, so a domain pack never writes ArcadeDB SQL -----------------------------

    def kg_read(
        self,
        node_type: str,
        *,
        where: Optional[dict[str, object]] = None,
        fields: Optional[list[str]] = None,
        distinct: Optional[str] = None,
        order_by: Optional[str] = None,
        limit: Optional[int] = None,
        key_range: Optional[tuple[str, object, object]] = None,
    ) -> list[dict]:
        """Read typed nodes of `node_type` (see `Store.kg_read`). A list `where` value that is empty is
        scope-to-nothing -> `[]` without a query (never an invalid `IN []`). Clauses AND-ed in insertion order,
        then `key_range` (`field >= lo AND field < hi`)."""
        clauses: list[str] = []
        for field, value in (where or {}).items():
            if isinstance(value, (list, tuple, set)):
                vals = list(value)
                if not vals:
                    return []
                clauses.append(f"{field} IN {_kg_sql_array(vals)}")
            else:
                clauses.append(f"{field} = {_kg_sql_value(value)}")
        if key_range is not None:
            f, lo, hi = key_range
            clauses += [f"{f} >= {_kg_sql_value(lo)}", f"{f} < {_kg_sql_value(hi)}"]
        proj = f"DISTINCT({distinct}) AS {distinct}" if distinct else (", ".join(fields) if fields else "*")
        sql = f"SELECT {proj} FROM {node_type}"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        if order_by:
            sql += f" ORDER BY {order_by}"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        return self._query(sql)

    def kg_edges(
        self,
        from_type: Optional[str] = None,
        *,
        where: Optional[dict[str, object]] = None,
        key_range: Optional[tuple[str, object, object]] = None,
        direction: str = "out",
        edge_type: Optional[str] = None,
        edge_where: Optional[dict[str, object]] = None,
        target_where: Optional[dict[str, object]] = None,
        select: dict[str, str],
    ) -> list[dict]:
        """Generic edge traversal (see `Store.kg_edges`): node-start MATCH (out/in) when a start selector is
        given, else a direct edge-table scan. An empty membership anywhere scopes to nothing -> `[]`."""

        c_terms = _kg_where_terms(where, key_range)
        e_terms, v_terms = _kg_where_terms(edge_where), _kg_where_terms(target_where)
        if c_terms is None or e_terms is None or v_terms is None:
            return []
        returns = ", ".join(f"{expr} AS {alias}" for alias, expr in select.items())

        if not (where or key_range is not None):  # --- edge-scan idiom ---
            if not edge_type:
                raise ValueError("kg_edges edge-scan needs an edge_type (no start selector given)")
            sql = f"SELECT {returns} FROM {edge_type}"
            if e_terms:
                sql += " WHERE " + " AND ".join(e_terms)
            return self._query(sql)

        # --- node-start MATCH traversal ---
        if from_type is None:
            raise ValueError("kg_edges traversal needs a from_type")
        etok = f"'{edge_type}'" if edge_type else ""
        if direction == "out":
            edge_step, far_step = f"outE({etok})", "inV()"
        elif direction == "in":
            edge_step, far_step = f"inE({etok})", "outV()"
        else:
            raise ValueError(f"kg_edges direction must be 'out' or 'in', got {direction!r}")

        def _blk(body: str, term_list: list[str]) -> str:
            return "{" + body + (f", where: ({' AND '.join(term_list)})" if term_list else "") + "}"

        c_blk = _blk(f"type: {from_type}, as: c", c_terms)
        e_blk = _blk("as: e", e_terms)
        v_blk = _blk("as: v", v_terms)
        return self._query(f"MATCH {c_blk}.{edge_step}{e_blk}.{far_step}{v_blk} RETURN {returns}")

    def kg_count(self, type_name: str, *, where: Optional[dict[str, object]] = None,
                 key_range: Optional[tuple[str, object, object]] = None) -> int:
        """Count the vertices or edges of `type_name` matching `where` / `key_range` (see `Store.kg_count`)."""
        terms = _kg_where_terms(where, key_range)
        if terms is None:
            return 0
        rows = self._query(f"SELECT count(*) AS n FROM {type_name}" + (" WHERE " + " AND ".join(terms) if terms else ""))
        return int(rows[0]["n"]) if rows else 0

    def _type_kind(self, type_name: str) -> str:
        for row in self._query("SELECT name, type FROM schema:types"):
            if row.get("name") == type_name:
                return str(row.get("type"))
        raise ValueError(f"unknown KG type: {type_name}")

    def kg_delete(self, type_name: str, *, where: Optional[dict[str, object]] = None,
                  key_range: Optional[tuple[str, object, object]] = None) -> int:
        """Delete the vertices (with their edges) or edges of `type_name` matching `where` / `key_range` (see
        `Store.kg_delete`). Edge types delete `UNSAFE` (this dialect's edge-safety check). Returns the count."""
        terms = _kg_where_terms(where, key_range)
        if terms is None:
            return 0
        sql = f"DELETE FROM {type_name}" + (" WHERE " + " AND ".join(terms) if terms else "")
        if self._type_kind(type_name) == "edge":
            sql += " UNSAFE"
        return _kg_changed_count(self._command(sql))

    def kg_update(self, type_name: str, *, set: dict[str, object], where: Optional[dict[str, object]] = None,
                  key_range: Optional[tuple[str, object, object]] = None) -> int:
        """Set fields on the vertices or edges of `type_name` matching `where` / `key_range`, changing only rows
        where a set field differs (see `Store.kg_update`). Values encode as `kg_write` encodes them. Returns the
        number of rows changed."""
        if not set:
            raise ValueError("kg_update needs at least one field to set")
        terms = _kg_where_terms(where, key_range)
        if terms is None:
            return 0
        types = self._property_types(type_name)
        enc = {k: _kg_encode(v, types.get(k)) for k, v in set.items()}
        differs = " OR ".join(f"{k} IS NULL OR {k} <> {v}" for k, v in enc.items())
        sql = (f"UPDATE {type_name} SET " + ", ".join(f"{k} = {v}" for k, v in enc.items())
               + " WHERE " + " AND ".join([*terms, f"({differs})"]))
        return _kg_changed_count(self._command(sql))

    def _property_types(self, type_name: str) -> dict[str, str]:
        """`{property -> declared storage type}` for a KG node type, used by `kg_write` to encode each prop. Sourced
        from the schemas of this store's packs (`schema_packs`). Cached per
        store (reset when a pack is ensured)."""
        cache = getattr(self, "_prop_types_cache", None)
        if cache is None:
            cache: dict[str, dict[str, str]] = {}
            for ttl in self.schema_packs():  # ING-8a: only the packs this store has -- none by default
                for vt in load_kg_schema(ttl)[0]:
                    cache[vt.name] = dict(vt.properties)
            self._prop_types_cache = cache
        return cache.get(type_name, {})

    def kg_write(self, nodes, edges=()) -> None:
        """Upsert typed `nodes` (by `key_field`) then create typed `edges`, all in one transaction (see
        `Store.kg_write`). Node props encode by the type's pack-declared storage type; edge props are type-driven."""
        statements: list[str] = []
        for n in nodes:
            types = self._property_types(n.type)
            sets = ", ".join(f"{k} = {_kg_encode(v, types.get(k))}" for k, v in n.props.items())
            key_sql = _kg_encode(n.props[n.key_field], types.get(n.key_field))
            statements.append(f"UPDATE {n.type} SET {sets} UPSERT WHERE {n.key_field} = {key_sql}")
        for e in edges:
            stmt = (
                f"CREATE EDGE {e.type}"
                f" FROM (SELECT FROM {e.from_type} WHERE {e.from_key_field} = {_kg_sql(e.from_key)})"
                f" TO (SELECT FROM {e.to_type} WHERE {e.to_key_field} = {_kg_sql(e.to_key)})")
            if e.props:
                stmt += " SET " + ", ".join(f"{k} = {_kg_sql(v)}" for k, v in e.props.items())
            statements.append(stmt)
        if statements:
            _retry_on_conflict(self._db.execute_transaction, statements)

    def kg_ensure_edges(self, edges) -> int:
        """ING-4b (see `Store.kg_ensure_edges`): create each typed edge only if no edge of its type already joins
        the same two endpoints; an existing one has its props UPDATED in place (a re-ingest never duplicates an
        edge, and a changed link confidence lands). Endpoint keys are matched with `outV()`/`inV()` (a bare
        `out.<prop>` projects NULL on this ArcadeDB, issue 0029). Returns the number of edges created."""
        created = 0
        for e in edges:
            where = (f" WHERE outV().{e.from_key_field} = {_kg_sql(e.from_key)}"
                     f" AND inV().{e.to_key_field} = {_kg_sql(e.to_key)}")
            rows = self._query(f"SELECT count(*) AS c FROM {e.type}{where}")
            props = ", ".join(f"{k} = {_kg_sql(v)}" for k, v in e.props.items())
            if rows and (rows[0].get("c") or 0) > 0:
                if props:
                    self._command(f"UPDATE {e.type} SET {props}{where}")
                continue
            stmt = (f"CREATE EDGE {e.type}"
                    f" FROM (SELECT FROM {e.from_type} WHERE {e.from_key_field} = {_kg_sql(e.from_key)})"
                    f" TO (SELECT FROM {e.to_type} WHERE {e.to_key_field} = {_kg_sql(e.to_key)})")
            self._command(stmt + (f" SET {props}" if props else ""))
            created += 1
        return created

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
        pages = "[" + ",".join(str(int(p)) for p in record.pages) + "]"  # issue 0032: source page(s)
        tags_json = _sql_str(json.dumps(list(record.tags)))  # ADR-0114: top-k soft tags (primary-first)
        bbox = "null" if record.bbox is None else _sql_str(json.dumps(list(record.bbox)))  # best-effort [l,t,r,b]
        self._command(
            f"UPDATE {SPAN_TYPE} SET"
            f" span_id = {_sql_str(record.span_id)},"
            f" parent_chunk_id = {_sql_str(record.parent_chunk_id)},"
            f" span_index = {int(record.span_index)},"
            f" text = {_sql_str(record.text)},"
            f" primary_tag = {_sql_str(record.primary_tag)},"
            f" dense = {dense},"
            f" sparse_indices = {sparse_indices},"
            f" sparse_weights = {sparse_weights},"
            f" document_id = {_sql_str(record.document_id)},"  # CU-B2: citation + within-document filter
            f" doc_start = {doc_start},"
            f" doc_end = {doc_end},"
            f" pages = {pages},"  # issue 0032 (CU-B5): source page(s) for the citation highlight
            f" tags = {tags_json},"  # ADR-0114: top-k soft tags (JSON list, primary-first)
            f" bbox = {bbox}"
            f" UPSERT WHERE span_id = {_sql_str(record.span_id)}"
        )

    def spans_by_document(self, document_id: str, primary_tags: list[str]) -> list[dict]:
        """The within-document tag filter (CU-B3; ING-8e, was `spans_by_contract`) -- every span of `document_id`
        whose `primary_tag` is in `primary_tags`, ordered by document position (empty `primary_tags` -> []).
        Returns citation-ready rows (span_id, parent pointer, text, primary_tag, doc offsets)."""
        if not primary_tags:
            return []
        return self.kg_read(SPAN_TYPE, fields=[
            "span_id", "parent_chunk_id", "span_index", "text", "primary_tag",
            "document_id", "doc_start", "doc_end", "pages", "bbox"],
            where={"document_id": document_id, "primary_tag": primary_tags}, order_by="doc_start")

    def all_spans_by_document(self, document_id: str) -> list[dict]:
        """EVERY span of one document (all tags), with its dense vector, ordered by document position (CU-C2;
        ING-8e, was `all_spans_by_contract`). For an in-document semantic fallback: one document is small (hundreds
        of spans), so ranking happens in Python -- a global ANN + document filter would miss. Returns citation-ready
        rows plus `dense`."""
        return self._query(
            f"SELECT span_id, parent_chunk_id, span_index, text, primary_tag, document_id,"
            f" doc_start, doc_end, pages, bbox, dense FROM {SPAN_TYPE}"  # issue 0032: page citation
            f" WHERE document_id = {_sql_str(document_id)} ORDER BY doc_start"
        )

    def span_hybrid_search(
        self,
        dense_query: list[float],
        sparse_query: dict[int, float],
        *,
        k: int,
        primary_tag: str | None = None,
        documents: list[str] | None = None,
    ) -> list[dict]:
        """RRF-fused dense+sparse search over the `Span` index, optionally restricted to one `primary_tag`
        (the span tagger's routing filter, FR-R) and/or a `documents` set (issue 0031: a workspace
        scope -- `Span.document_id IN [...]`, the source-document id). Mirrors `hybrid_search`; returns span_id
        + the parent pointer so the caller can follow the span back to its clause for the rerank stage.

        Issue 0031: when `documents` is given, the vector legs pull a LARGER pool (`SCOPED_CANDIDATE_POOL`)
        before the `document_id IN [...]` cut, because the KNN ranks across the whole index and only then is
        scoped -- a small workspace could otherwise under-fill `k` from the default pool. `documents=[]` is a
        valid scope-to-nothing -> no query (`[]`)."""
        if documents is not None and not documents:
            return []  # scope-to-nothing: never issue an invalid `IN []`
        leg_k = max(k, SCOPED_CANDIDATE_POOL if documents else DEFAULT_CANDIDATE_POOL)
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
        clauses = []
        if primary_tag:
            clauses.append(f"primary_tag = {_sql_str(primary_tag)}")
        if documents:
            clauses.append(f"document_id IN {_str_array(documents)}")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        return self._query(
            f"SELECT span_id, parent_chunk_id, primary_tag FROM ({fused}){where} LIMIT {k}"
        )

    def span_dense_search(
        self,
        dense_query: list[float],
        *,
        k: int,
        documents: list[str] | None = None,
    ) -> list[dict]:
        """Issue 0041 (dense floor): PURE-DENSE nearest-neighbour search over the `Span` dense index -- the dense
        leg of `span_hybrid_search` WITHOUT the sparse leg or RRF fusion, so a strong semantic match a short
        common-token query's sparse leg would crowd out of the fused pool is still recoverable. Returns the same
        row shape as `span_hybrid_search` (span_id + parent pointer + primary_tag), in descending cosine order.
        `documents` scopes to a workspace exactly as the hybrid search does (`document_id IN [...]`, with the
        larger scoped pool before the cut); `documents=[]` is scope-to-nothing."""
        if documents is not None and not documents:
            return []
        leg_k = max(k, SCOPED_CANDIDATE_POOL if documents else DEFAULT_CANDIDATE_POOL)
        dense = _float_array(dense_query)
        neighbours = f"SELECT expand(`vector.neighbors`('{_SPAN_DENSE_INDEX}', {dense}, {leg_k}))"
        where = f" WHERE document_id IN {_str_array(documents)}" if documents else ""
        return self._query(
            f"SELECT span_id, parent_chunk_id, primary_tag FROM ({neighbours}){where} LIMIT {k}"
        )

    def span_texts(self, span_ids: list[str]) -> dict[str, str]:
        """The operative-span text for each span_id (batched), for citing a retrieved span. {span_id: text}."""
        if not span_ids:
            return {}
        id_list = "[" + ",".join(_sql_str(s) for s in span_ids) + "]"
        rows = self._query(f"SELECT span_id, text FROM {SPAN_TYPE} WHERE span_id IN {id_list}")
        return {r["span_id"]: r.get("text", "") for r in rows}

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
        its extracted entities land together. Uses `execute_transaction` so a failure rolls back whole.

        Idempotent (issue 0029 / ADR-0092): re-ingesting the same document CONVERGES instead of appending.
        Nodes stay UPSERT (already idempotent). A `Mentions` edge is created only if absent on (chunk,
        entity); a `Relationship` edge only if absent on (source, target, relationship_type, chunk_id) --
        so a genuinely distinct edge from a different contract (a different provenance `chunk_id`) still
        writes, while a re-ingest of the same document adds nothing. Existence is checked with read-only
        pre-queries BEFORE the transaction; correctness no longer depends on a caller's `already_ingested`
        guard (which stays a useful whole-pipeline short-circuit)."""
        statements: list[str] = []
        for node in nodes:  # nodes first, so edge endpoints exist within the transaction
            statements.append(
                f"UPDATE {ENTITY_TYPE} SET"
                f" entity_id = {_sql_str(node.node_key)},"
                f" canonical_id = {_sql_str(node.entity_id)},"  # ADR-0067 P5c: the resolver's canonical id
                f" name = {_sql_str(node.name)},"
                f" entity_type = {_sql_str(node.entity_type)},"
                f" confidence = {_sql_str(node.confidence)},"
                f" chunk_id = {_sql_str(node.chunk_id)}"
                f" UPSERT WHERE entity_id = {_sql_str(node.node_key)}"
            )
        existing_chunks = self._existing_chunks({node.chunk_id for node in nodes})
        seen_mentions: set[tuple[str, str]] = set()  # de-dupe within this batch too (full convergence)
        for node in nodes:
            key = (node.chunk_id, node.node_key)
            if node.chunk_id in existing_chunks and key not in seen_mentions \
                    and not self._mentions_edge_exists(node.chunk_id, node.node_key):
                seen_mentions.add(key)
                statements.append(  # connect chunk -> entity (provenance edge)
                    f"CREATE EDGE {MENTIONS_EDGE_TYPE}"
                    f" FROM (SELECT FROM {CHUNK_TYPE} WHERE chunk_id = {_sql_str(node.chunk_id)})"
                    f" TO (SELECT FROM {ENTITY_TYPE} WHERE entity_id = {_sql_str(node.node_key)})"
                )
        seen_edges: set[tuple[str, str, str, str]] = set()
        for edge in edges:
            key = (edge.source_key, edge.target_key, edge.relationship_type, edge.chunk_id)
            if key in seen_edges or self._relationship_edge_exists(
                    edge.source_key, edge.target_key, edge.relationship_type, edge.chunk_id):
                continue
            seen_edges.add(key)
            statements.append(
                f"CREATE EDGE {REL_EDGE_TYPE}"
                f" FROM (SELECT FROM {ENTITY_TYPE} WHERE entity_id = {_sql_str(edge.source_key)})"
                f" TO (SELECT FROM {ENTITY_TYPE} WHERE entity_id = {_sql_str(edge.target_key)})"
                f" SET relationship_type = {_sql_str(edge.relationship_type)},"
                f" confidence = {_sql_str(edge.confidence)}, {_edge_provenance_assignments(edge.chunk_id)}"
            )
        if statements:
            _retry_on_conflict(self._db.execute_transaction, statements)

    def _mentions_edge_exists(self, chunk_id: str, node_key: str) -> bool:
        """True iff a `Mentions` edge already connects this chunk to this entity (issue 0029 idempotence).
        Endpoint properties are reached with `outV()`/`inV()`: a plain `out.<prop>`/`in.<prop>` projection
        returns NULL on this ArcadeDB (verified live), which would silently defeat the existence check."""
        rows = self._query(
            f"SELECT count(*) AS c FROM {MENTIONS_EDGE_TYPE}"
            f" WHERE outV().chunk_id = {_sql_str(chunk_id)} AND inV().entity_id = {_sql_str(node_key)}")
        return bool(rows) and (rows[0].get("c") or 0) > 0

    def _relationship_edge_exists(self, source_key: str, target_key: str, rel_type: str, chunk_id: str) -> bool:
        """True iff a `Relationship` edge already exists on (source, target, relationship_type, chunk_id)
        -- the full provenance key, so distinct edges from different contracts are not collapsed (0029).
        `outV()`/`inV()` reach the endpoint entity_ids (a bare `out.entity_id` projects NULL here)."""
        rows = self._query(
            f"SELECT count(*) AS c FROM {REL_EDGE_TYPE}"
            f" WHERE relationship_type = {_sql_str(rel_type)}"
            f" AND outV().entity_id = {_sql_str(source_key)} AND inV().entity_id = {_sql_str(target_key)}"
            f" AND chunk_id = {_sql_str(chunk_id)}")
        return bool(rows) and (rows[0].get("c") or 0) > 0

    def add_affiliation_edges(self, nodes: list[GraphNode], edges: list[GraphEdge]) -> int:
        """issue 0027 BACKFILL (edge-only): add `AFFILIATE_OF` edges to an ALREADY-INGESTED KG without re-writing
        it. Creates an entity node ONLY IF ABSENT (never overwrites an existing node's name/id -- unlike
        `write_graph`'s UPSERT, so a party node keeps its resolved identity), and creates each edge ONLY IF ABSENT.
        Idempotent -- re-running adds nothing. Returns the number of edges created. Used by
        `scripts/backfill_affiliations.py`; the live ingest path uses `write_graph` unchanged."""
        for node in nodes:
            if self._query(f"SELECT entity_id FROM {ENTITY_TYPE} WHERE entity_id = {_sql_str(node.node_key)} LIMIT 1"):
                continue  # keep the existing node exactly as it is
            self._command(
                f"INSERT INTO {ENTITY_TYPE} SET entity_id = {_sql_str(node.node_key)},"
                f" canonical_id = {_sql_str(node.entity_id)}, name = {_sql_str(node.name)},"
                f" entity_type = {_sql_str(node.entity_type)}, confidence = {_sql_str(node.confidence)},"
                f" chunk_id = {_sql_str(node.chunk_id)}")
        added = 0
        for edge in edges:
            exists = self._query(  # outV()/inV(): a bare out.entity_id projects NULL on this ArcadeDB (issue 0029)
                f"SELECT count(*) AS c FROM {REL_EDGE_TYPE}"
                f" WHERE relationship_type = {_sql_str(edge.relationship_type)}"
                f" AND outV().entity_id = {_sql_str(edge.source_key)} AND inV().entity_id = {_sql_str(edge.target_key)}")
            if exists and (exists[0].get("c") or 0) > 0:
                continue
            self._command(
                f"CREATE EDGE {REL_EDGE_TYPE}"
                f" FROM (SELECT FROM {ENTITY_TYPE} WHERE entity_id = {_sql_str(edge.source_key)})"
                f" TO (SELECT FROM {ENTITY_TYPE} WHERE entity_id = {_sql_str(edge.target_key)})"
                f" SET relationship_type = {_sql_str(edge.relationship_type)},"
                f" confidence = {_sql_str(edge.confidence)}, {_edge_provenance_assignments(edge.chunk_id)}")
            added += 1
        return added


    def known_document_ids(self) -> set[str]:
        """Issue 0031 / ING-8a: the DISTINCT ids of every INGESTED document -- the generic `Document` nodes, one per
        ingested source document (ING-4b; the reference contract pipeline writes them too). The validation set for
        a `documents` scope: a scope naming a document that was never ingested raises (mirrors issue 0007's
        `requirement_sources`), but a document that WAS ingested yet indexed nothing (unreadable / empty -> no
        spans, no edges) is a KNOWN document and PASSES -- it contributes nothing to the sweep, which the product
        reports through its coverage line rather than having one bad document break the sweep. The `Document`
        type may not exist yet on a fresh DB -> empty."""
        if DOCUMENT_TYPE not in self.type_names():
            return set()
        return {r["doc_id"] for r in self._query(f"SELECT doc_id FROM {DOCUMENT_TYPE}") if r.get("doc_id")}

    def entities_by_name(self, name: str) -> list[dict]:
        """Resolve a party NAME to its graph entities (issue 0030 / ADR-0093): the first step before
        `graph_neighbors`/`graph_query`, which take an exact `start_entity_id` and cannot be reached from a
        name otherwise. Returns `[{entity_id, name, entity_type}]` for every stored entity whose name
        normalizes to the same clustering key as `name`, via the SAME `normalize_entity_name` the ingestion
        side uses to cluster ('Acme Corp' / 'Acme Corporation' / 'ACME, Inc.' -> one key). A name may resolve
        to SEVERAL nodes (a resolved node plus a not-yet-merged unlinked ref) -- all are returned, each usable
        as a `start_entity_id`. Normalization is the engine's rule and is applied HERE (a raw or an already-
        normalized name both work; the normalization is idempotent). Empty list on no match / a non-entity name.

        (issue 0030 replaces the retired `all_entities`, which was removed with the KG-7 PartyTo capability but
        was the only name->entity route; this puts the capability on the Store seam and owns the normalization
        rather than forcing every caller to re-implement it against an engine internal.)"""
        target = normalize_entity_name(name)
        if not target:  # empty / whitespace / non-entity: no lookup key
            return []
        rows = self._query(f"SELECT entity_id, name, entity_type FROM {ENTITY_TYPE}")
        return [
            {"entity_id": r["entity_id"], "name": r["name"], "entity_type": r["entity_type"]}
            for r in rows
            if normalize_entity_name(r.get("name") or "") == target
        ]

    def graph_counts(self) -> dict[str, int]:
        entities = self._query(f"SELECT count(*) AS n FROM {ENTITY_TYPE}")
        rels = self._query(f"SELECT count(*) AS n FROM {REL_EDGE_TYPE}")
        return {
            "entities": int(entities[0]["n"]) if entities else 0,
            "relationships": int(rels[0]["n"]) if rels else 0,
        }

    def graph_neighbors(
        self, entity_id: str, *, relationship_type: str, max_hops: int, documents: list[str] | None = None
    ) -> list[dict]:
        """Traverse via ArcadeDB `MATCH` over `Relationship` edges (grounded live, T26): `bothE` binds
        each edge (so its `chunk_id`/`confidence` are cited) and `bothV` the reached entity. One-hop and
        two-hop are separate MATCH queries; `$matched` de-dups the two-hop return to the start. Braces
        are concatenated in (they clash with f-string interpolation).

        Issue 0031: `documents` scopes the traversal to a workspace's source documents -- EVERY edge on the
        path must have `source_doc_id IN [...]` (applied to e1 AND e2, so a two-hop path cannot route THROUGH
        an out-of-scope contract to reach an in-scope target). `None` = the whole graph; `[]` = scope-to-
        nothing (no query)."""
        if documents is not None and not documents:
            return []  # scope-to-nothing: never issue an invalid `IN []`
        eid = _sql_str(entity_id)
        rel = _sql_str(relationship_type)
        # each edge's where-clause: relationship_type, plus (0031) the document scope on the edge itself
        doc_scope = f" and source_doc_id IN {_str_array(documents)}" if documents else ""
        edge_where = "(relationship_type = " + rel + doc_scope + ")"
        paths: list[dict] = []

        one_hop = (
            "MATCH {type: " + ENTITY_TYPE + ", as: a, where: (entity_id = " + eid + ")}"
            ".bothE('" + REL_EDGE_TYPE + "'){as: e, where: " + edge_where + "}"
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
                ".bothE('" + REL_EDGE_TYPE + "'){as: e1, where: " + edge_where + "}"
                ".bothV(){as: b, where: (entity_id <> " + eid + ")}"
                ".bothE('" + REL_EDGE_TYPE + "'){as: e2, where: " + edge_where + "}"
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
        return _retry_on_conflict(self._db.query, "sql", sql, is_command=True)

    def _query(self, sql: str) -> list[dict]:
        result = self._db.query("sql", sql)
        return result if isinstance(result, list) else []
