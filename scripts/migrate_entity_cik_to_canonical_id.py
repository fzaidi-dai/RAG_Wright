"""ADR-0067 P5c ONE-TIME MIGRATION: on an existing KG, add Entity.canonical_id and backfill it from the old
SEC-specific Entity.cik column (the engine now writes canonical_id; cik was the EDGAR assumption). Idempotent
and additive -- the old cik column is left in place (harmless) for rollback; a later cleanup may drop it.

Usage: uv run python scripts/migrate_entity_cik_to_canonical_id.py <db> [<db> ...]
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# load .env so the script is runnable standalone (ARCADEDB_HOST etc.)
_env = Path(__file__).resolve().parents[1] / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from rag_wright.store.arcadedb import ENTITY_TYPE, ArcadeDBStore


def migrate(db: str) -> None:
    store = ArcadeDBStore.from_env(database=db)
    if not store.ping():
        print(f"{db}: does not exist -- skipped")
        return
    props = store.property_names(ENTITY_TYPE)
    if "canonical_id" not in props:
        store._command(f"CREATE PROPERTY {ENTITY_TYPE}.canonical_id STRING")
    if "cik" in props:
        # backfill only where not already set -> idempotent (a re-run is a no-op)
        store._command(f"UPDATE {ENTITY_TYPE} SET canonical_id = cik WHERE canonical_id IS NULL")
        n = next(iter(store._query(f"SELECT count(*) AS n FROM {ENTITY_TYPE} WHERE canonical_id IS NOT NULL")),
                 {}).get("n", "?")
        print(f"{db}: backfilled canonical_id = cik ({n} entities now carry canonical_id)")
    else:
        print(f"{db}: no legacy cik column (fresh or already migrated) -- nothing to backfill")
    store.close()


if __name__ == "__main__":
    for database in sys.argv[1:] or ["ragwright_cuad_full"]:
        migrate(database)
