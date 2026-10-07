"""PROD-2 Phase 1 (ADR-0049): ingest a NON-FTC-255 regulation into a SCRATCH Requirement KG and inspect it, to
validate that the compliance extraction GENERALIZES beyond the single tuned part. Mirrors ingest_ftc_compliance
but parameterized (SECTIONS / SOURCE / COMPLIANCE_DB) and with a fuller KG inspection.

  SECTIONS=data/compliance/ftc_16cfr233/ftc_16cfr233.sections.json SOURCE="FTC 16 CFR 233" \
  COMPLIANCE_DB=ragwright_compliance_prod2 uv run --no-sync python -m scripts.ingest_compliance_prod2
"""
from __future__ import annotations

import asyncio
import os
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv


def log(m: str) -> None:
    print(m, flush=True)


async def main() -> None:
    load_dotenv("/Users/farhan/work/RAG_Wright/.env")
    os.environ.setdefault("RAG_SERVING", "openrouter")
    from rag_wright.packs.contracts.capabilities.dg_extraction import default_extraction_model
    from rag_wright.store.arcadedb import REQUIREMENT_TYPE, ArcadeDBStore
    from rag_wright.packs.compliance.subgraphs.compliance_ingestion import run_compliance_ingestion

    sections = Path(os.environ.get("SECTIONS", "data/compliance/ftc_16cfr233/ftc_16cfr233.sections.json"))
    source = os.environ.get("SOURCE", "FTC 16 CFR 233")
    db = os.environ.get("COMPLIANCE_DB", "ragwright_compliance_prod2")
    reset = os.environ.get("RESET", "1") == "1"

    store = ArcadeDBStore.from_env(database=db, reset=reset)
    model = default_extraction_model("requirement-extract", "ibm-granite/granite-4.1-8b")
    log(f"[prod2] ingesting {sections.name} ({source}) -> {db!r} (reset={reset})")

    report = await run_compliance_ingestion(sections, store, model=model, source=source)

    n = store._query(f"SELECT count(*) AS n FROM {REQUIREMENT_TYPE}")[0]["n"]
    rows = store.all_requirements()
    log(f"\n=== PROD-2 COMPLIANCE INGEST REPORT ({source}) ===")
    log(f"sections ingested: {report.documents_ingested} | dead-lettered: {len(report.dead_lettered)}")
    for d in report.dead_lettered:
        log(f"  DEAD-LETTER {d.get('source_doc_id')}: {d.get('stage')}/{d.get('error')}")
    log(f"Requirement nodes: {n}")
    log(f"deontic types: {dict(Counter(r['deontic_type'] for r in rows))}")
    with_scope = sum(1 for r in rows if r["applicability_json"] not in ("[]", "", None))
    log(f"with applicability scope: {with_scope}/{n}")
    # per-section requirement counts (over/under-generation signal on a new rulebook)
    by_section: Counter = Counter(r.get("citation") or "?" for r in rows)
    log("requirements per citation (over/under-generation check):")
    for sec, c in by_section.most_common():
        log(f"  {c:3d}  {sec}")
    log("\nsample requirements (text sanity on the new domain):")
    for r in rows[:6]:
        log(f"  [{r['deontic_type']}] {(r.get('requirement_text') or '')[:90]}")
    store.close()
    log("\n[prod2] done.")


if __name__ == "__main__":
    asyncio.run(main())
