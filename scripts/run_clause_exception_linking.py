"""ADR-0044: run the `clause_exception_linking` pass (derive + write the cap<->carve-out `IsExceptionTo`
edges) against whatever KG the ARCADEDB_* env points at. Reusable by the co-located Modal job
(scripts/modal_exception_linking.py, the [[gcp-bulk-ingestion-box]] pattern) and locally.

The pass is pure/deterministic (proximity over span doc-offsets, no LLM) and idempotent (clears + rederives
the IsExceptionTo layer), so re-running is safe.
"""

from __future__ import annotations

import os

from rag_wright.capabilities.clause_exception_linking import clause_exception_linking
from rag_wright.store.arcadedb import IS_EXCEPTION_TO_EDGE_TYPE, ArcadeDBStore


def main() -> None:
    db = os.environ.get("ARCADEDB_DATABASE", "ragwright_cuad_full")
    store = ArcadeDBStore.from_env(database=db)
    # the edge type predates ADR-0044, so it may be absent on an already-ingested KG -> create it (guarded)
    if IS_EXCEPTION_TO_EDGE_TYPE not in store.type_names():
        store._command(f"CREATE EDGE TYPE {IS_EXCEPTION_TO_EDGE_TYPE}")
        print(f"[link] created edge type {IS_EXCEPTION_TO_EDGE_TYPE}", flush=True)
    print(f"[link] deriving cap<->carve-out IsExceptionTo links on {db} ...", flush=True)
    result = clause_exception_linking(store)
    n = store._query(f"SELECT count(*) AS n FROM {IS_EXCEPTION_TO_EDGE_TYPE}")[0]["n"]
    print(
        f"[link] contracts_processed={result.contracts_processed}"
        f" links_written={len(result.links)}"
        f" unlinked_exceptions={result.unlinked_exceptions}"
        f" edges_in_kg={n}",
        flush=True,
    )
    store.close()


if __name__ == "__main__":
    main()
