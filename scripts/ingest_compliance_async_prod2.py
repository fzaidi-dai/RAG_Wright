"""COMP-ASYNC-1 (PROD-2 backlog #3+#5): LIVE async compliance ingestion -- submit a regulation through
`submit_compliance_ingestion`, returning a job_id immediately, sections ingesting in parallel, status polled from
the JobStore. Brings the compliance side to parity with the PROD-3 contract async+lossless envelope.

  SECTIONS=data/compliance/ftc_16cfr233/ftc_16cfr233.sections.json SOURCE="FTC 16 CFR 233" \
  COMPLIANCE_DB=ragwright_compliance_async uv run --no-sync python -m scripts.ingest_compliance_async_prod2
"""
from __future__ import annotations

import os
import time
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv


def log(m: str) -> None:
    print(m, flush=True)


def main() -> None:
    load_dotenv("/Users/farhan/work/RAG_Wright/.env")
    os.environ.setdefault("RAG_SERVING", "openrouter")
    from rag_wright.capabilities.dg_extraction import default_extraction_model
    from rag_wright.store.arcadedb import REQUIREMENT_TYPE, ArcadeDBStore
    from rag_wright.subgraphs.async_ingestion import JobStore
    from rag_wright.subgraphs.compliance_ingestion import RegulationAdapter, submit_compliance_ingestion

    sections = Path(os.environ.get("SECTIONS", "data/compliance/ftc_16cfr233/ftc_16cfr233.sections.json"))
    source = os.environ.get("SOURCE", "FTC 16 CFR 233")
    db = os.environ.get("COMPLIANCE_DB", "ragwright_compliance_async")

    store = ArcadeDBStore.from_env(database=db, reset=os.environ.get("RESET", "1") == "1")
    store.database = db
    model = default_extraction_model("requirement-extract", "ibm-granite/granite-4.1-8b")
    jobs = JobStore("data/cache/compliance_async/jobs")
    adapter = RegulationAdapter(sections, source=source)

    job_id = submit_compliance_ingestion(
        adapter, store, jobs, job_id="comp-233", model=model, source=source, max_concurrency=3)
    log(f"[comp-async] submit_compliance_ingestion RETURNED IMMEDIATELY -> job_id={job_id}. Polling ...")

    last = -1
    while True:
        job = jobs.get(job_id)
        if job.documents_done != last:
            log(f"[comp-async] status={job.status.value} {job.documents_done}/{job.documents_total} "
                f"(ingested={job.ingested} dead_lettered={len(job.dead_lettered)})")
            last = job.documents_done
        if job.done:
            break
        time.sleep(3)

    n = store._query(f"SELECT count(*) AS n FROM {REQUIREMENT_TYPE}")[0]["n"]
    rows = store.all_requirements()
    log(f"\n=== COMP-ASYNC-1 LIVE RESULT ===\nstatus={job.status.value} ingested={job.ingested} "
        f"dead_lettered={len(job.dead_lettered)}")
    log(f"Requirement nodes: {n} | deontic: {dict(Counter(r['deontic_type'] for r in rows))}")
    ok = job.status.value == "succeeded" and n >= 1
    log(f"\n[comp-async] {'PASS' if ok else 'CHECK'}: async submit->parallel->status-poll->completion on the "
        f"compliance side (non-blocking, pollable, lossless dead-letter).")
    store.close()


if __name__ == "__main__":
    main()
