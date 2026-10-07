# Running ArcadeDB locally (T13/T14)

The single store (FR-S.1) runs as a local Docker container during development. Confirmed against
Pinned to **ArcadeDB 26.7.1** (the latest stable tag; `:latest` resolves to a moving `-SNAPSHOT`
dev build, which we deliberately do not use). This is the version T13's schema and the `vector.fuse`
de-risk are validated on. Sparse vector index and server-side hybrid retrieval exist since 26.5.1.

## Start

```bash
docker run -d --name arcadedb-ragwright \
  -p 2480:2480 -p 2424:2424 \
  -v "$PWD/data/arcadedb:/home/arcadedb/databases" \
  -e JAVA_OPTS="-Darcadedb.server.rootPassword=<YOUR_DEV_PASSWORD> -Darcadedb.server.mode=development" \
  arcadedata/arcadedb:26.7.1
```

- Port `2480` is the HTTP API the `arcadedb_python` client uses; `2424` is the binary protocol.
- Data persists under the gitignored `data/arcadedb/` (SPEC data directory; never committed).
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
id and vector indexes. A domain's types come from its pack (`EngineConfig(pack=...)`); the reference contract
pipeline creates `Clause`, `Contract`, `PropertyValue` and its typed edges the first time it runs.

## Stop / reset

```bash
docker stop arcadedb-ragwright && docker rm arcadedb-ragwright   # stop (data persists in the volume)
rm -rf data/arcadedb                                            # wipe all local databases
```
