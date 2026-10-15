# ArcadeDB for engine development

**This page is for working on the engine itself.** A product runs its own ArcadeDB instance and owns its data,
including the knowledge graph: see [Run your product's ArcadeDB](installation.md#run-your-products-arcadedb-the-single-store).
The instance below is the engine developer's own: it holds the databases the engine's live store tests
(`pytest -m store`) and its evals build, on public or locally acquired evaluation corpora. No product points at it,
and no product's data goes in it.

It runs as a local Docker container, pinned to **ArcadeDB 26.7.1** (the latest stable tag; `:latest` resolves to a
moving `-SNAPSHOT` dev build, which we deliberately do not use). This is the version the engine's schema and the
`vector.fuse` hybrid retrieval are validated on. Sparse vector index and server-side hybrid retrieval exist since
26.5.1.

## Start

```bash
docker run -d --name arcadedb-ragwright \
  -p 2480:2480 -p 2424:2424 \
  -v "$PWD/data/arcadedb:/home/arcadedb/databases" \
  -e JAVA_OPTS="-Darcadedb.server.rootPassword=<YOUR_DEV_PASSWORD> -Darcadedb.server.mode=development" \
  -e ARCADEDB_OPTS_MEMORY="-Xms2G -Xmx6G" \
  arcadedata/arcadedb:26.7.1
```

- Port `2480` is the HTTP API the `arcadedb_python` client uses; `2424` is the binary protocol.
- Data persists under this checkout's gitignored `data/arcadedb/`: engine test and eval databases only, never
  committed.
- `ARCADEDB_OPTS_MEMORY` sets the JVM heap, the store's capacity limit (the image default is `-Xms2G -Xmx2G`, which
  ran out of memory part-way through the full CUAD evaluation knowledge graph). It is read at container start, so changing it
  means recreating the container.
- Set the same password in `.env` as `ARCADEDB_PASSWORD` (with `ARCADEDB_HOST=localhost`,
  `ARCADEDB_PORT=2480`, `ARCADEDB_USER=root`, `ARCADEDB_DATABASE=rag_wright`). `.env` is gitignored.

## Verify it is up

```bash
curl -s -u root:<YOUR_DEV_PASSWORD> http://localhost:2480/api/v1/databases
```

## Run the store-backed tests

The store tests are opt-in (they need this container). `conftest.py` loads `.env` and gates them:

```bash
uv run pytest -m store        # runs the live ArcadeDB tests
uv run pytest                 # default suite skips them (hermetic)
```

The default suite is not just skipped but guarded: `tests/conftest.py` fails any test that is not marked live and
tries to reach the network. The live markers (declared in `pyproject.toml`) are `model`, `store`, `parse`, `embed`,
`rerank`, `ner` and `fleet`.

## What a new database contains

`open_workspace` (or `ensure_schema`) gives a new database only the neutral engine schema: the vertex types `Chunk`,
`Entity`, `Span` and `Document`, the edge types `Relationship`, `Mentions`, `EmbeddedIn` and `AttachedTo`, and their
id and vector indexes. A domain's types come from its pack (`EngineConfig(pack=...)`). The reference pack's store
extensions create theirs when constructed: `ContractKGStore` creates `Clause`, `Contract`, `PropertyValue` and the
typed edges, and `ComplianceStore` creates `Requirement`, so they appear the first time the reference pipelines run.

A database created before the `Span` field rename (`contract_id`, `function`, `functions` became `document_id`,
`primary_tag`, `tags`) is refused by `ensure_schema` with a `RuntimeError`; migrate it once with
`migrate_span_fields(config, corpus=...)` from `rag_wright.api` ([installation](installation.md)).

## Stop / reset

```bash
docker stop arcadedb-ragwright && docker rm arcadedb-ragwright   # stop (data persists in the volume)
rm -rf data/arcadedb                                            # wipe the engine's local test databases
```
