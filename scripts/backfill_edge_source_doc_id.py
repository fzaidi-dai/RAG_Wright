#!/usr/bin/env python
"""issue 0031 backfill: stamp `source_doc_id` on the Relationship edges of an ALREADY-INGESTED KG.

The workspace document scope on `graph_query` (ADR-0094) filters a traversal on each edge's `source_doc_id`,
which ingestion now writes (derived from the edge's provenance `chunk_id`). A graph built BEFORE that change
has edges with no `source_doc_id`, so a scoped traversal would match none of them. This script adds the
property in place -- it never touches endpoints, `relationship_type`, `chunk_id`, or confidence.

Per DISTINCT provenance `chunk_id` present on Relationship edges:
    source_doc_id = <the prefix before the first ':' of chunk_id>   (the id scheme is <doc>:<idx>:<hash>)
    UPDATE Relationship SET source_doc_id = '<doc>' WHERE chunk_id = '<chunk_id>' AND source_doc_id IS NULL

Idempotent: an edge that already has `source_doc_id` is skipped (the `IS NULL` guard), so re-running is a
no-op. Streams X/N progress. Run against a COPY of the DB first if you want to inspect the writes.

Usage:
    uv run python scripts/backfill_edge_source_doc_id.py --database <arcadedb db>
"""
from __future__ import annotations

import argparse

from dotenv import load_dotenv


def log(msg: str) -> None:
    print(msg, flush=True)


def _run(database: str) -> None:
    from rag_wright.store.arcadedb import REL_EDGE_TYPE, ArcadeDBStore, _doc_id_of, _sql_str

    store = ArcadeDBStore.from_env(database=database)
    if REL_EDGE_TYPE not in store.type_names():
        log(f"[backfill] no {REL_EDGE_TYPE} edges in '{database}'; nothing to do.")
        return

    rows = store._query(f"SELECT DISTINCT(chunk_id) AS c FROM {REL_EDGE_TYPE}")
    chunk_ids = [r["c"] for r in rows if r.get("c")]
    n = len(chunk_ids)
    log(f"[backfill] {database}: {n} distinct edge chunk_id(s) to stamp with source_doc_id")

    updated_groups = 0
    for i, chunk_id in enumerate(chunk_ids, 1):
        doc_id = _doc_id_of(chunk_id)
        if not doc_id:
            log(f"[backfill] {i}/{n} SKIP (no doc prefix): {chunk_id!r}")
            continue
        # only the edges still missing the property (idempotent re-run)
        store._command(
            f"UPDATE {REL_EDGE_TYPE} SET source_doc_id = {_sql_str(doc_id)}"
            f" WHERE chunk_id = {_sql_str(chunk_id)} AND source_doc_id IS NULL")
        updated_groups += 1
        if i % 50 == 0 or i == n:
            log(f"[backfill] {i}/{n} stamped (doc={doc_id})")

    log(f"[backfill] DONE: {updated_groups}/{n} chunk_id group(s) processed for '{database}'.")


def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser(description="Backfill Relationship.source_doc_id (issue 0031).")
    ap.add_argument("--database", required=True, help="the ArcadeDB database to backfill in place")
    args = ap.parse_args()
    _run(args.database)


if __name__ == "__main__":
    main()
