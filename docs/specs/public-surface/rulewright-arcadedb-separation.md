# RuleWright's databases move to RuleWright's own ArcadeDB

**From:** the engine, 2026-10-12 · **Ledger:** PS-34 (`plan.md`) · **Not tied to a release:** nothing in the engine
package changes; this is about where data lives on the development machine.

## Why

The store and everything in it belong to the product: the retrieval index and the knowledge graph are the
product's data. The engine is install, import and config, and holds no product data. The engine's docs now say so
(`docs/installation.md`, "Run your product's ArcadeDB"; `docs/ArcadeDB_Local.md` is the engine developer's own test
instance only). RuleWright's `CLAUDE.md` already says the same and lists this move as an open task to do jointly.

## What is wrong today (checked 2026-10-12)

- One Docker container, `arcadedb-ragwright`, serves both repos' databases from the **engine checkout's**
  `RAG_Wright/data/arcadedb`, on port 2480.
- That directory holds RuleWright's databases, matched by pattern (`rulewright_*`, `rw_*`; the count moves with
  every run, 21 on 2026-10-13): its two keepers `rulewright_dev` and `rulewright_compliance`, 15 finished-task
  scratch databases, two test fixtures live tests recreate, and two leftover test tenants. It also holds the
  engine's own: `ragwright_cuad_full` (1.1 GB), `ragwright_prod1`, `ragwright_prod1_async`,
  `ragwright_journey_incidents`, `quickstart_demo`, `model_server_example`.
- **`scripts/arcadedb.sh` defaults to the same container name** (`ARCADEDB_CONTAINER` defaults to
  `arcadedb-ragwright`), so RuleWright's script managed the engine's container and set its memory (a 3 GiB
  container cap, `-Xms1G -Xmx2G`). The engine restored its own settings on 2026-10-13 (below).

## The move (owner: RuleWright, in its repo)

1. **Its own container and port.** Change `scripts/arcadedb.sh`'s default container name to a RuleWright one (for
   example `arcadedb-rulewright`) and give it its own host port (for example 2481), so both instances can exist
   side by side. Its data directory stays its own `data/arcadedb/` (the script's default already).
2. **Copy with the engine's container stopped**, so the copy is consistent:
   `docker stop arcadedb-ragwright`, then copy each `rulewright_*` and `rw_*` directory you keep from
   `RAG_Wright/data/arcadedb/` into RuleWright's `data/arcadedb/`, matched by pattern, never from a list (a copy,
   not a move: the engine deletes its copies only after step 4). The scratch and disposable databases are dropped
   instead of copied, through the engine: `drop_workspace(config, corpus=...)` (PS-37; in the next release, or now through a path dependency).
3. **Point RuleWright at it:** its `.env` takes the new port (and container name if anything reads it); start it
   with `./scripts/arcadedb.sh up`. The script's guard against an empty data directory then passes because the
   data is there.
4. **Confirm:** every moved database opens through the engine with `create=False` (0.8.0, PS-27: a missing
   database is refused, not silently created), and RuleWright's live store tests pass against the new instance.
   Then tell the engine.

## Then (owner: engine)

- Delete the RuleWright databases from `RAG_Wright/data/arcadedb/` (after the user's go-ahead: it cannot be undone).

Done on 2026-10-13: `some-db` (empty; the engine's, from before PS-27) dropped; the engine's container recreated on
its own settings (6 GB heap, no container cap); the two stopped containers removed.

## So it does not regrow

PS-37 adds `drop_workspace` and `temporary_workspace` to `rag_wright.api`. A probe or measurement that needs its own
database opens it with `with temporary_workspace(config, prefix="probe") as ws:`, and the database is dropped when
the block exits, also on error; see `docs/workspaces.md`, "Removing a workspace".
