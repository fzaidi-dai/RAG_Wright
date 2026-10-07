"""ING-8d: migrate an existing database's `Span` records to the generic field names.

`contract_id` -> `document_id`, `function` -> `primary_tag`, `functions` -> `tags` (values copied, old fields removed,
old properties dropped). The legacy `parent_okf_path` locator is left in place. Idempotent: re-running on a migrated
database moves nothing. After ING-8d, `ArcadeDBStore.ensure_schema` refuses an unmigrated database.

Usage: uv run python -u scripts/migrate_span_fields.py <database> [--batch 5000]
"""
from __future__ import annotations

import argparse
import time

from dotenv import load_dotenv

from rag_wright.store.arcadedb import ArcadeDBStore


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("database")
    ap.add_argument("--batch", type=int, default=5000)
    args = ap.parse_args()
    load_dotenv(".env")
    store = ArcadeDBStore.from_env(database=args.database)
    t0 = time.time()
    print(f"[migrate] {args.database}: Span field rename (batch {args.batch})", flush=True)
    moved = store.migrate_span_fields(
        batch=args.batch,
        progress=lambda done, total: print(f"[migrate] {done}/{total} spans, {time.time() - t0:.0f}s", flush=True))
    store.ensure_schema()  # the post-condition: the migrated database is accepted
    print(f"[migrate] done: {moved} spans moved in {time.time() - t0:.0f}s"
          + (" (already migrated)" if moved == 0 else ""), flush=True)
    store.close()


if __name__ == "__main__":
    main()
