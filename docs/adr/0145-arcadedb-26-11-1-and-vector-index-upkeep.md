# ADR-0145: ArcadeDB 26.11.1, with a start-up warm and a one-time rebuild of the vector indexes

**Status:** accepted · **Date:** 2026-11-12 · **Related:** PS-67, TexWright's request
`2026-11-12-the-span-vector-index-is-rebuilt-on-every-process-start.md`, ADR-0007 (the hybrid SQL on 26.7.1), ADR-0008
(vendor-doc grounding), PS-50 (pack properties), PS-57 (the conflict retry)

## Context

On ArcadeDB 26.7.1 an `LSM_VECTOR` index that holds any deleted or rewritten vector rebuilds its graph from scratch
on every open, and every span UPSERT (any re-ingest) writes one. TexWright's deployment on Modal scales to zero and
keeps ingesting through its UI, so every cold start paid the rebuild in a user's first search (80 s there).
`REBUILD INDEX` clears it only until the next re-ingest, and on 26.7.1 it deadlocks the database when run during
searches. Nothing the engine writes can avoid it on 26.7.1.

On 26.11.1, measured on 10,000 real span vectors: a database built, re-ingested (1,000 spans) and restarted three
times loads its saved graph each time (first search 0.21 s against 1.46 s); a graph is rebuilt only after about 20%
of an index has changed. But the first 26.11.1 open of a database 26.7.1 wrote rebuilds the graph by itself, and that
rebuild left 139,938 of 139,955 vectors on `ragwright_cuad_full` (8,834 of 8,851 on TexWright's) reachable only
by the delta scan, which gives correct results by reading every vector. An explicit `REBUILD INDEX` on 26.11.1 left
1,765 (search 7.9 ms against 50 ms), and on 26.11.1 it ran during searches without a deadlock (5 runs, about 850
concurrent searches each, none failed).

## Decision

- The engine is tested on and pins **ArcadeDB 26.11.1** (`docs/installation.md`, `docs/ArcadeDB_Local.md`, the Modal
  scripts). Its jars still target Java 21.
- `rag_wright.api.warm_vector_indexes(ws)`: one neighbour search per engine dense index holding a vector, timed, for a
  deployment's start-up before it serves.
- `rag_wright.api.rebuild_vector_indexes(ws)`: `REBUILD INDEX` on each engine dense index, run once per database after
  the move from 26.7.1, before serving. Both are on the `Store` seam.
- Two 26.10+ behaviours the engine meets: a write conflict worded "concurrent change to the same record" is retried
  like the old one, and a pack property ArcadeDB refuses to declare over stored values not of its type is logged and
  left undeclared, then declared by a later `ensure_pack_schema` once a re-ingest has rewritten the values.

## Consequences

- A deployment that keeps ingesting no longer rebuilds at every start; a rebuild after a large change lands at
  start-up when it warms.
- Each product moves its own instances and deploy scripts to 26.11.1, backs up, and runs `rebuild_vector_indexes`
  once per database. 26.10 and 26.11 also roll back a failed statement on its own, read `UPDATE ... SET` against the
  row as it was, convert types more strictly and answer bad credentials with 401.
- Heavy exact duplication among vectors still leaves some of them scan-only after any build (1,231 of 8,851 on
  TexWright's data); the results stay correct. That is ArcadeDB's to fix (issue #7190's area).
