"""PROD-1 (ADR-0049): ingest the non-CUAD corpus FROM GCS through the GENERIC pipeline into a SCRATCH KG
(`ragwright_prod1`, never the production KG). Proves the real path end to end on real non-CUAD data:
GCS -> (parse) -> chunk -> segment -> classify -> granite property extraction + judges -> typed KG + span index.

Deliberately GENERIC (tests generalization): an EMPTY entity registry (no CUAD/EDGAR verified anchors) and NO
party seed. Classifier pinned to the validated config (Gemma-4-31b via coreweave/bf16 + fallbacks); extraction =
granite (product substrate) falling back off coreweave automatically. Vision is irrelevant here (text corpus).

  LIMIT=2 uv run --no-sync python -m scripts.ingest_prod1        # smoke: 2 docs (NDAs first, smaller)
  LIMIT=0 uv run --no-sync python -m scripts.ingest_prod1        # full 100
"""
from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv


def log(m: str) -> None:
    print(m, flush=True)


async def main() -> None:
    load_dotenv("/Users/farhan/work/RAG_Wright/.env")  # SA creds (GCS) + ArcadeDB + OpenRouter
    os.environ.setdefault("RAG_SERVING", "openrouter")
    os.environ.setdefault("RAG_MODEL_GENERAL", "google/gemma-4-31b-it")   # validated classifier model
    os.environ.setdefault("OPENROUTER_PROVIDER", "coreweave/bf16")        # Gemma -> coreweave; granite -> fallback
    os.environ.setdefault("OPENROUTER_ALLOW_FALLBACKS", "true")

    limit = int(os.environ.get("LIMIT", "2"))
    db = os.environ.get("PROD1_DB", "ragwright_prod1")
    reset = os.environ.get("RESET", "1") == "1"
    bucket = os.environ.get("GCS_BUCKET", "dreamai-pocs-ragwright-ingest")
    prefix = os.environ.get("GCS_PREFIX", "prod1-corpus/")
    # INCLUDE_FILE: a curated subset (one blob basename per line) -> ingest exactly those (budget-bounded MVP set)
    include = None
    inc_file = os.environ.get("INCLUDE_FILE")
    if inc_file and os.path.exists(inc_file):
        with open(inc_file) as f:
            include = frozenset(ln.strip() for ln in f if ln.strip())

    from rag_wright.capabilities.dg_extraction import build_verified_registry
    from rag_wright.capabilities.party_clause_linking import party_clause_linking
    from rag_wright.corpus.gcs_ingestion import production_gcs_adapter
    from rag_wright.store.arcadedb import ArcadeDBStore
    from rag_wright.subgraphs.contract_ingestion_pipeline import (
        aproduction_document_ingest,
        arun_corpus_ingestion,
    )

    log(f"[prod1] scratch DB={db} reset={reset} | GCS gs://{bucket}/{prefix} "
        f"{'include='+str(len(include))+' curated' if include else 'limit='+str(limit or 'ALL')}")
    store = ArcadeDBStore.from_env(database=db, reset=reset)
    store.ensure_schema()
    registry = build_verified_registry({"entities": []})  # GENERIC: no CUAD/EDGAR verified entity anchors
    ingest_graph = aproduction_document_ingest(
        store, cache_dir="data/cache/prod1", registry=registry, party_seed_path=None)  # no CUAD party seed
    adapter = production_gcs_adapter(bucket, prefix, limit=0 if include else limit, include=include)

    log("[prod1] ingesting from GCS ...")
    report = await arun_corpus_ingestion(
        adapter, ingest_graph, progress=log,
        # GENERIC party->clause linking (KG-7), no cache: the single-provenance join straight from the KG's own
        # extracted parties. (The CUAD many-to-many enrichment needs a mention cache; this cacheless path is the
        # generic default.) Runs ONCE after all documents are written.
        link_fn=lambda: len(party_clause_linking(store).links),
        is_done=lambda doc: store.contract_by_id(doc.source_doc_id) is not None)

    log("\n=== PROD-1 INGEST REPORT ===")
    log(f"documents_ingested: {report.documents_ingested} | dead_lettered: {len(report.dead_lettered)}")
    for dl in report.dead_lettered[:10]:
        log(f"  DEAD-LETTER {dl.get('source_doc_id','?')[:50]}: {str(dl.get('reason',''))[:80]}")

    # --- KG inspection: did the pipeline produce a sane KG + populate the NEW ONT-2 dimensions? ---
    def q1(sql):
        r = store._query(sql)
        return r[0].get("n", 0) if r else 0

    log("\n=== KG produced ===")
    log(f"Contract={q1('SELECT count(*) AS n FROM Contract')}  Clause={q1('SELECT count(*) AS n FROM Clause')}  "
        f"Span={q1('SELECT count(*) AS n FROM Span')}  Entity={q1('SELECT count(*) AS n FROM Entity')}")
    log("top function labels:")
    for r in store._query("SELECT function, count(*) AS n FROM Clause GROUP BY function ORDER BY n DESC LIMIT 12"):
        log(f"  {r['n']:4d}  {r['function']}")
    none = q1("SELECT count(*) AS n FROM Clause WHERE function = 'NONE'")
    log(f"function=NONE: {none}")
    log("\nNEW ONT-2 dimension edges populated (the generic-lens payoff):")
    for et in ("HAS_DISPUTE_METHOD", "SECURES", "HAS_FORCE_MAJEURE_EVENT", "HAS_ROYALTY_BASIS"):
        if et in store.type_names():
            log(f"  {et}: {q1(f'SELECT count(*) AS n FROM {et}')}")
    log("\n[prod1] DONE. Inspect `ragwright_prod1`; scale with LIMIT=0 when the smoke looks right.")
    store.close()


if __name__ == "__main__":
    asyncio.run(main())
