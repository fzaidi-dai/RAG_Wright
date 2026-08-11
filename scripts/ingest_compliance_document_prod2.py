"""PROD-2 Phase 2 (DOCPARSE-1): LIVE end-to-end ingest of a customer's OWN policy DOCUMENT (bytes) into the
Requirement KG -- docling parse -> heading-split sections -> the SAME compliance pipeline. Proves the
customer-PDF/DOCX path (via DocumentRegulationAdapter), the generic-input half the eCFR-XML producer couldn't do.

  DOC=<path> SOURCE="..." COMPLIANCE_DB=ragwright_compliance_docparse uv run --no-sync python -m scripts.ingest_compliance_document_prod2
"""
from __future__ import annotations

import os
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
    from rag_wright.subgraphs.compliance_ingestion import run_compliance_document_ingestion

    doc_path = Path(os.environ["DOC"])
    source = os.environ.get("SOURCE", doc_path.stem)
    db = os.environ.get("COMPLIANCE_DB", "ragwright_compliance_docparse")

    store = ArcadeDBStore.from_env(database=db, reset=os.environ.get("RESET", "1") == "1")
    model = default_extraction_model("requirement-extract", "ibm-granite/granite-4.1-8b")
    data = doc_path.read_bytes()
    log(f"[docparse-2] ingesting DOCUMENT {doc_path.name} ({len(data)} bytes, {source}) -> {db!r} "
        f"via docling parse -> sections -> Requirement KG")

    report = run_compliance_document_ingestion(doc_path.name, data, store, model=model, source=source)

    n = store._query(f"SELECT count(*) AS n FROM {REQUIREMENT_TYPE}")[0]["n"]
    rows = store.all_requirements()
    log("\n=== PROD-2 PHASE-2 (customer document) RESULT ===")
    log(f"sections ingested: {report.documents_ingested} | dead-lettered: {len(report.dead_lettered)} | "
        f"partial: {len(report.partial)}")
    for d in report.dead_lettered:
        log(f"  DEAD-LETTER {d.get('source_doc_id')}: {d.get('stage')}/{d.get('error')}")
    log(f"Requirement nodes: {n} | deontic: {dict(Counter(r['deontic_type'] for r in rows))}")
    for r in rows[:6]:
        log(f"  [{r['deontic_type']}] {(r.get('requirement_text') or '')[:90]}")
    ok = report.documents_ingested >= 1 and n >= 1 and not report.dead_lettered
    log(f"\n[docparse-2] {'PASS' if ok else 'CHECK'}: a customer DOCUMENT ingested end to end "
        f"(bytes -> docling -> sections -> Requirement KG).")
    store.close()


if __name__ == "__main__":
    main()
