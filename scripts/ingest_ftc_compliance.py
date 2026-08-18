"""CC-5 (compliance §13): ingest the FTC 16 CFR 255 sections into the Requirement KG.

Runs the `compliance_ingestion` subgraph over `data/compliance/ftc_16cfr255/16cfr255.sections.json` into a
SEPARATE database (`ragwright_compliance`), so the contract KG stays clean. Streams `[ingest] X/N` progress
(CLAUDE.md long-running rule). Model = granite via the seam (RAG_SERVING: openrouter dev / vllm product).

  RESET=1 RAG_SERVING=openrouter uv run --no-sync python -m scripts.ingest_ftc_compliance
"""

from __future__ import annotations

import asyncio
import os
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv


async def main() -> None:
    load_dotenv()
    from rag_wright.capabilities.dg_extraction import default_extraction_model
    from rag_wright.store.arcadedb import REQUIREMENT_TYPE, ArcadeDBStore
    from rag_wright.subgraphs.compliance_ingestion import run_compliance_ingestion

    db = os.environ.get("COMPLIANCE_DB", "ragwright_compliance")
    reset = os.environ.get("RESET", "1") == "1"  # RESET=0 to resume onto an existing partial DB
    store = ArcadeDBStore.from_env(database=db, reset=reset)
    model = default_extraction_model("requirement-extract", "ibm-granite/granite-4.1-8b")
    sections = Path("data/compliance/ftc_16cfr255/16cfr255.sections.json")
    print(f"[compliance] ingesting {sections} -> {db!r} (reset={reset})", flush=True)

    report = await run_compliance_ingestion(sections, store, model=model, source="FTC 16 CFR 255")

    n = store._query(f"SELECT count(*) AS n FROM {REQUIREMENT_TYPE}")[0]["n"]
    rows = store.all_requirements()
    print(f"[compliance] REPORT: {report.documents_ingested} sections ingested, "
          f"{len(report.dead_lettered)} dead-lettered", flush=True)
    for d in report.dead_lettered:
        print(f"[compliance]   DEAD-LETTER {d.get('source_doc_id')}: {d.get('stage')}/{d.get('error')}", flush=True)
    print(f"[compliance] Requirement nodes: {n}", flush=True)
    print(f"[compliance] deontic: {dict(Counter(r['deontic_type'] for r in rows))}", flush=True)
    print(f"[compliance] with applicability scope: "
          f"{sum(1 for r in rows if r['applicability_json'] not in ('[]', '', None))}/{n}", flush=True)
    store.close()
    print("[compliance] done.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
