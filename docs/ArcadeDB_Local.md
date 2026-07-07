# Running ArcadeDB locally (T13/T14)

The single store (FR-S.1) runs as a local Docker container during development. Confirmed against
**ArcadeDB 26.7.2** (image `arcadedata/arcadedb:latest`), the version T13's schema and the
`vector.fuse` de-risk were validated on.

## Start

```bash
docker run -d --name arcadedb-ragwright \
  -p 2480:2480 -p 2424:2424 \
  -v "$PWD/data/arcadedb:/home/arcadedb/databases" \
  -e JAVA_OPTS="-Darcadedb.server.rootPassword=<YOUR_DEV_PASSWORD> -Darcadedb.server.mode=development" \
  arcadedata/arcadedb:latest
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

## Stop / reset

```bash
docker stop arcadedb-ragwright && docker rm arcadedb-ragwright   # stop (data persists in the volume)
rm -rf data/arcadedb                                            # wipe all local databases
```
