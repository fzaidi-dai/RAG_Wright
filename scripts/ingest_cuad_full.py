"""CUAD-FULL-COVERAGE (b): ingest ALL 510 CUAD contracts UNIFORMLY through the generic LG-3d pipeline into a
FRESH database, so every stage (clause KG, entity graph, Span retrieval index, PARTY_TO link) covers the same
510 -- fixing the inconsistent-scale KG (entity graph ~482, clause KG ~100, spans 77). Non-destructive: builds
into `ragwright_cuad_full` (reset=True on THAT db), leaving the live `ragwright_cuad` intact until we verify +
adopt.

INGEST-REFACTOR (a) makes this cheap: the per-contract GP-1B party extractions (~482) and prior chunk manifests
(102) are reused from cache; the only unavoidable cost is the clause-property-extraction pass (the clause
template changed since those were cached). Streams `[ingest] i/510` progress (CLAUDE.md long-running rule).

  FULL_DB=ragwright_cuad_full uv run python -m scripts.ingest_cuad_full
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv


async def main() -> None:
    load_dotenv()
    from rag_wright.corpus.cuad_ingestion import arun_cuad_ingestion
    from rag_wright.store.arcadedb import CONTRACT_TYPE, SPAN_TYPE, ArcadeDBStore

    db = os.environ.get("FULL_DB", "ragwright_cuad_full")
    cache_dir = Path("data/cache/cuad_full")  # persistent: a re-run reuses everything already extracted
    reset = os.environ.get("RESET", "1") == "1"  # RESET=0 to RESUME onto an existing partial db (don't wipe it)
    store = ArcadeDBStore.from_env(database=db, reset=reset)  # FRESH build -- live ragwright_cuad untouched
    print(f"[full] CUAD-FULL-COVERAGE: ingesting ALL CUAD docs through the generic pipeline into {db!r}",
          flush=True)

    report = await arun_cuad_ingestion(
        Path("data/cuad/extracted/CUAD_v1.json"), store, cache_dir=cache_dir, limit=0)

    dl = report.dead_lettered
    print(f"[full] REPORT: {report.documents_ingested} ingested, {len(dl)} dead-lettered, "
          f"{report.party_links} PARTY_TO edges", flush=True)
    if dl:
        for d in dl[:20]:
            print(f"[full]   DEAD-LETTER {d.get('source_doc_id')}: {d.get('stage')}/{d.get('reason')}", flush=True)

    # Verify uniform coverage across every stage -- the point of CUAD-FULL-COVERAGE.
    clause_kg = store.clause_kg_counts()
    graph = store.graph_counts()
    spans = store._query(f"SELECT count(*) AS n FROM {SPAN_TYPE}")[0]["n"]
    contracts = store._query(f"SELECT count(*) AS n FROM {CONTRACT_TYPE}")[0]["n"]
    print("[full] COVERAGE:", flush=True)
    print(f"[full]   Contract nodes:   {contracts}", flush=True)
    print(f"[full]   clause KG:        {clause_kg}", flush=True)
    print(f"[full]   entity graph:     {graph}", flush=True)
    print(f"[full]   Span index:       {spans}", flush=True)
    print(f"[full]   PARTY_TO edges:   {party_to}", flush=True)
    store.close()
    print("[full] done -- verify counts, then adopt (swap in for ragwright_cuad) if uniform + healthy.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
