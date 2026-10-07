"""ING-4b (ADR-0124): LIVE smoke of `build_ingestion` against a real ArcadeDB + the real ingest embedder.

Ingests every document in `$RAG_EVAL_TEXTILE_DIR` (LOCAL ONLY: client data) -- including the files embedded in
them -- into a scratch database, with a deterministic test extractor (one `Record` node per unit; no model calls),
then verifies the store against the report and re-runs the whole ingest to prove it is idempotent:

  - every document ingested (or dead-lettered with a reason), every embedded child a `Document` with an
    `EmbeddedIn` edge, every record link an `AttachedTo` edge whose row span really holds the evidence token;
  - Span / Record / Document / edge counts match the report and are UNCHANGED by the second run.

Run: `RAG_EVAL_TEXTILE_DIR=/path HF_HUB_OFFLINE=1 uv run python -u eval/ingestion_live_smoke.py`
Writes `data/eval/ingestion_live/report.json`. Drops and recreates the scratch database `rw_scratch_ing4b`.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "eval" / "ingestion_live"
DB = "rw_scratch_ing4b"


def log(msg: str) -> None:
    print(msg, flush=True)


async def record_extractor(unit, *, source_doc_id):
    from rag_wright.api import KgNode, UnitExtraction

    return UnitExtraction(nodes=[KgNode("Record", "record_id", {
        "record_id": f"{source_doc_id}:{unit.index}", "span_id": unit.anchor.span_id, "confidence": "EXTRACTED",
        "text": unit.text[:200]})])


def counts(store) -> dict:
    def n(sql: str) -> int:
        rows = store._query(sql)
        return int(rows[0].get("c") or 0) if rows else 0

    return {t: n(f"SELECT count(*) AS c FROM {t}") for t in ("Span", "Record", "Document", "EmbeddedIn", "AttachedTo")}


def main() -> None:
    from dotenv import load_dotenv

    from rag_wright.api import EngineConfig, StoreConfig, build_ingestion, open_workspace
    from rag_wright.store.arcadedb import ArcadeDBStore

    load_dotenv(ROOT / ".env")
    src = os.environ.get("RAG_EVAL_TEXTILE_DIR")
    if not src:
        raise SystemExit("set RAG_EVAL_TEXTILE_DIR")
    files = sorted(str(p) for p in Path(src).iterdir() if p.suffix.lower() in (".pdf", ".xlsx", ".xlsm", ".docx"))
    ArcadeDBStore.from_env(database=DB, reset=True).close()  # a clean scratch database
    ws = open_workspace(EngineConfig(store=StoreConfig(
        host=os.environ["ARCADEDB_HOST"], port=os.environ["ARCADEDB_PORT"], user=os.environ["ARCADEDB_USER"],
        password=os.environ["ARCADEDB_PASSWORD"])), corpus=DB)
    store = ws._store
    if "Record" not in store.type_names():
        store._command("CREATE VERTEX TYPE Record")
        store._command("CREATE PROPERTY Record.record_id STRING")
        store._command("CREATE INDEX ON Record (record_id) UNIQUE")
    pipeline = build_ingestion(record_extractor)
    log(f"[live] run 1: {len(files)} source files")
    report = asyncio.run(pipeline.aingest(ws, files, cache_dir=OUT / "cache"))
    first = counts(store)
    log(f"[live] store after run 1: {first}")

    docs = report.documents
    ok = [d for d in docs if d.dead_letter is None]
    children = [d for d in docs if d.parent_doc_id]
    links = sum(sum(d.links.values()) for d in docs)
    checks = {
        "documents_match": first["Document"] == len(ok),
        "spans_match": first["Span"] == sum(d.spans for d in ok),
        "records_match": first["Record"] == sum(d.records for d in ok),
        "embedded_in_per_child": first["EmbeddedIn"] == sum(len(d.children) for d in docs),
        "attached_to_match": first["AttachedTo"] == links,
        "no_unmapped_links": sum(d.unmapped_links for d in docs) == 0,
    }
    rows = store._query("SELECT outV().doc_id AS child, inV().text AS row, evidence, basis FROM AttachedTo")
    with_evidence = [r for r in rows if r.get("evidence")]
    checks["evidence_on_attached_row"] = all(
        any(tok in (r.get("row") or "").lower() for tok in r["evidence"].split()) for r in with_evidence)

    log("[live] run 2 (idempotence)")
    asyncio.run(pipeline.aingest(ws, files, cache_dir=OUT / "cache"))
    second = counts(store)
    log(f"[live] store after run 2: {second}")
    checks["idempotent"] = first == second

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps({"documents": [asdict(d) for d in docs], "store": first,
                                                 "checks": checks}, indent=2, default=str))
    log(f"[live] documents={len(docs)} ok={len(ok)} dead_lettered={report.failed} children={len(children)} "
        f"links={links} extraction_failures={sum(len(d.extraction_failures) for d in docs)}")
    for d in docs:
        if d.dead_letter:
            log(f"[live]   DEAD-LETTER {d.doc_id}: {d.dead_letter[:160]}")
    log(f"[live] checks {checks} -> {'PASS' if all(checks.values()) else 'FAIL'}")
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
