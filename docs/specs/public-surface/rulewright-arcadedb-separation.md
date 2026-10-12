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
- That directory holds **25 RuleWright databases**: `rulewright_dev` (113 MB), `rulewright_compliance`, and 23
  `rw_*` databases (test and run databases, including the two `rw_<id>_contract` / `rw_<id>_compliance` tenant
  databases). It also holds the engine's own: `ragwright_cuad_full` (1.1 GB), `ragwright_prod1`,
  `ragwright_prod1_async`, `ragwright_journey_incidents`, `quickstart_demo`, `model_server_example`, `some-db`.
- **`scripts/arcadedb.sh` defaults to the same container name** (`ARCADEDB_CONTAINER` defaults to
  `arcadedb-ragwright`), so RuleWright's script manages the engine's container. Its memory settings (a 3 GiB
  container cap, `-Xms1G -Xmx2G`) are the ones live on that container now; the engine's docs assume a 6 GB heap for
  its 1.1 GB evaluation graph.

## The move (owner: RuleWright, in its repo)

1. **Its own container and port.** Change `scripts/arcadedb.sh`'s default container name to a RuleWright one (for
   example `arcadedb-rulewright`) and give it its own host port (for example 2481), so both instances can exist
   side by side. Its data directory stays its own `data/arcadedb/` (the script's default already).
2. **Copy with the engine's container stopped**, so the copy is consistent:
   `docker stop arcadedb-ragwright`, then copy each `rulewright_*` and `rw_*` directory from
   `RAG_Wright/data/arcadedb/` into RuleWright's `data/arcadedb/` (a copy, not a move: the engine deletes its
   copies only after step 4).
3. **Point RuleWright at it:** its `.env` takes the new port (and container name if anything reads it); start it
   with `./scripts/arcadedb.sh up`. The script's guard against an empty data directory then passes because the
   data is there.
4. **Confirm:** every moved database opens through the engine with `create=False` (0.8.0, PS-27: a missing
   database is refused, not silently created), and RuleWright's live store tests pass against the new instance.
   Then tell the engine.

## Then (owner: engine)

- Delete the RuleWright databases from `RAG_Wright/data/arcadedb/` (after the user's go-ahead: it cannot be undone).
- Decide `some-db` (1.8 MB, looks like a leftover).
- Restore the engine container's own settings from `docs/ArcadeDB_Local.md`, recreating it if needed.

## One constraint to plan for

Docker's VM on this machine has about 7.65 GiB, and the Langfuse stack takes about 4.1 GiB of it. Two ArcadeDB
containers at a 3 GiB cap each plus Langfuse do not fit at once. Run one ArcadeDB at a time locally (stop the
engine's while working on RuleWright, and the other way round), or size both down together; never run one with a
container limit below its `-Xmx` (RuleWright's script explains why).
