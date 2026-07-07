# ADR-0007: The ArcadeDB store seam and hybrid schema

Date: 2026-07-07. Status: Accepted. Records the store seam and the ArcadeDB schema/indexes built at
T13, and the empirical findings from standing up ArcadeDB 26.7.1 that shaped them (risk 2, the v0.x
client and version behavior).

## Grounding (two surfaces, no Java)

The store spans two grounding surfaces, both Python-appropriate:

- **The client API is `arcadedb-python` 0.4.0** (the officially recommended Python driver, latest
  release), indexed in the Graphify framework graph. It is the grounding authority for connection,
  database lifecycle, `query()`/command execution, dense `vector_search`/`create_vector_index`, and
  the bulk operations. Every client call T13 makes resolves against it.
- **The SQL vector functions are not wrapped by the Python API**, so hybrid retrieval is issued as
  SQL through the grounded `query()` method, and that SQL's authority is the **official ArcadeDB
  documentation** (docs.arcadedb.com), not the server jar. Confirmed forms: dense
  `vector.neighbors('T[dense]', vec, k)`; sparse `vector.sparseNeighbors('T[idx,wt]', qIdx, qWt, k)`;
  hybrid `SELECT expand(vector.fuse(<dense>, <sparse>, { fusion: 'RRF' }))`; sparse index
  `CREATE INDEX ON T (idx, wt) LSM_SPARSE_VECTOR METADATA { dimensions: <vocab>, modifier: 'IDF' }`.
  The Java engine is not a grounding target: this is a Python project against the Python API + the
  vendor's SQL docs.

The server is pinned to the stable **26.7.1** tag (not the moving `:latest`/`-SNAPSHOT`), so the
build is reproducible and matches the documented SQL.

## Context

FR-S.1 puts the hybrid retrieval index and the knowledge graph in one ArcadeDB store, reached behind
a swappable seam (FR-S.5) so the eval-gated LanceDB fallback (GATE-2) can replace the retrieval leg
without touching capability code. T13 defines that seam and the schema: a chunk-record type carrying
the dense summary vector and the sparse full-text vector, and a graph-node type, both keyed by
`chunk_id`, with a dense `LSM_VECTOR` index and a sparse `LSM_SPARSE_VECTOR` index.

The build stood up ArcadeDB locally (Docker, `arcadedata/arcadedb:26.7.1`, port 2480; see
`docs/ArcadeDB_Local.md`) and sanity-checked the primitives before writing code, because the client
is v0.x and the index behavior was unverified.

## Decision

**Seam.** `store/seam.py` defines `Store`, a `runtime_checkable` Protocol that is semantic, not SQL:
`ensure_schema`, `type_names`, `property_names`, `index_names`, `ping`, `close`. No query string
crosses the seam, so a second implementation binds it without inheriting ArcadeDB's dialect. The
ArcadeDB implementation (`store/arcadedb.py`) sits behind it; an in-memory stub in the tests proves
swappability (the LanceDB-fallback shape). Write-side and query-side methods are added by the tasks
that need them (T20, T21, T26); T13 defines only the schema-management surface.

**Schema.** One database with two vertex types:

- `Chunk` (hybrid index): `chunk_id` (STRING, UNIQUE), `source_doc_id` (STRING), `dense`
  (ARRAY_OF_FLOATS), `sparse_indices` (ARRAY_OF_INTEGERS), `sparse_weights` (ARRAY_OF_FLOATS).
- `Entity` (graph node): `entity_id` (STRING, UNIQUE), `chunk_id` (STRING).

Both carry `chunk_id`, so a chunk and its extracted entities live in one store with no cross-store
join (FR-S.1, FR-I.4).

**Indexes.** Dense `CREATE INDEX ON Chunk (dense) LSM_VECTOR METADATA { dimensions: 1024, similarity:
'COSINE' }` (dimension bound to T3's `BGE_M3_DENSE_DIM`, not a magic number); sparse `CREATE INDEX ON
Chunk (sparse_indices, sparse_weights) LSM_SPARSE_VECTOR`; plus the two UNIQUE identifier indexes.

## Empirical findings (from the sanity probe against 26.7.1)

- **The sparse index needs two parallel arrays, not a map.** `LSM_SPARSE_VECTOR` rejects a single MAP
  property: "Sparse vector index requires 2 properties: an indices array (ARRAY_OF_INTEGERS) and a
  weights array (ARRAY_OF_FLOATS)". So T3's `sparse_vector: dict[int, float]` is **decomposed at the
  store boundary** into `sparse_indices` (sorted token ids) and `sparse_weights` (T20 writes it). The
  contract stays `dict[int, float]`; normalization lives at the boundary, not in the contract.
- **`IF NOT EXISTS` is unsupported in this DDL position**, so idempotency is by schema introspection:
  `ensure_schema` reads `schema:types` / `schema:indexes` and creates only what is absent. Re-running
  is a no-op (tested).
- **`vector.fuse` exists** (`SELECT vector.fuse([], [])` returns `[]`), which de-risks the server-side
  RRF fusion GATE-2 depends on. Proving it end-to-end with real ranked lists is T14.
- The query-side vector-search function name is not yet pinned (probes for `vectorNeighbors` /
  `vectorDistance` did not resolve); that grounding belongs to T14/T21, not the schema.

## Consequences

- T20 (chunk write) decomposes `sparse_vector` into the two arrays and upserts by `chunk_id`.
- T14 proves `vector.fuse` RRF over the dense and sparse legs end to end; T13 confirmed the primitive
  exists and the two indexes build.
- The ArcadeDB version is pinned to stable 26.7.1 for these findings; re-verify on upgrade.
- **Sparse index refinement (grounded post-hoc):** the docs show `LSM_SPARSE_VECTOR` takes
  `METADATA { dimensions: <vocab>, modifier: 'IDF' }` for BM25-style weighting. T13's index was
  created without it (it builds and is searchable). Setting `dimensions` (the BGE-M3 sparse vocab)
  and the IDF modifier is retrieval-quality tuning that the SPEC scopes to the sparse-vocabulary-cap
  and IDF-weighting retrieval parameters (section 16.5, i.e. T21/GATE-2), not the T13 schema shape.
- Local run and credentials are documented in `docs/ArcadeDB_Local.md`; the dev password lives only
  in gitignored `.env`, the data only under gitignored `data/arcadedb/`.
- The store tests are opt-in (`-m store`), kept out of the default hermetic suite by `conftest.py`
  (which also, fixed here, gates opt-in tests by marker rather than by keyword so hermetic tests
  under `tests/store/` still run).
