"""PROD-3 Increment 2d: LIVE validation of the ASYNC job-based ingestion (no MCP -- exposure is a product-spec
decision). Submits a few small NDAs from the GCS prod1 corpus through `submit_ingestion` into a scratch KG,
polls `get_ingestion_status` (JobStore) to completion, and includes a FORCED-FAILURE document to prove the
lossless invariant: a failed doc is DEAD-LETTERED (visible on the job), never silently dropped.

  uv run --no-sync python -m scripts.ingest_prod1_async
"""
from __future__ import annotations

import os
import time

from dotenv import load_dotenv


def log(m: str) -> None:
    print(m, flush=True)


# 3 small NDAs (fast) + a 4th that we FORCE to fail, to validate the dead-letter path end to end.
_DOCS = ["contractnli_nda_034.txt", "contractnli_nda_022.txt", "contractnli_nda_024.txt"]
_POISON = "contractnli_nda_016.txt"


class _PoisonGraph:
    """Wrap the real per-document ingest graph; RAISE for the poison doc so the async runner must dead-letter it
    (a deterministic stand-in for the rare live extraction transient). Every other doc runs the real graph."""

    def __init__(self, graph, poison_id: str) -> None:
        self._g = graph
        self._poison = poison_id

    def invoke(self, state):
        if state["document"].source_doc_id == self._poison:
            from rag_wright.packs.contracts.capabilities.dg_extraction import ExtractionFailed

            raise ExtractionFailed("party", "FORCED failure (2d validation): simulated extraction crash")
        return self._g.invoke(state)


def main() -> None:
    load_dotenv("/Users/farhan/work/RAG_Wright/.env")  # SA creds (GCS) + ArcadeDB + OpenRouter
    os.environ.setdefault("RAG_SERVING", "openrouter")
    os.environ.setdefault("RAG_MODEL_GENERAL", "google/gemma-4-31b-it")
    os.environ.setdefault("OPENROUTER_PROVIDER", "coreweave/bf16")
    os.environ.setdefault("OPENROUTER_ALLOW_FALLBACKS", "true")

    from rag_wright.packs.contracts.capabilities.dg_extraction import build_verified_registry
    from rag_wright.packs.contracts.corpus.gcs_ingestion import production_gcs_adapter
    from rag_wright.store.arcadedb import ArcadeDBStore
    from rag_wright.packs.contracts.subgraphs.async_ingestion import JobStore, submit_ingestion
    from rag_wright.packs.contracts.subgraphs.contract_ingestion_pipeline import aproduction_document_ingest

    db = os.environ.get("PROD1_DB", "ragwright_prod1_async")
    bucket = "dreamai-pocs-ragwright-ingest"
    include = frozenset(_DOCS + [_POISON])
    log(f"[2d] scratch DB={db} | {len(include)} docs from GCS (1 FORCED failure: {_POISON})")

    store = ArcadeDBStore.from_env(database=db, reset=True)
    store.ensure_schema()
    ingest_graph = _PoisonGraph(
        aproduction_document_ingest(store, cache_dir="data/cache/prod1_async",
                                   registry=build_verified_registry({"entities": []}), party_seed_path=None),
        _POISON)
    adapter = production_gcs_adapter(bucket, "prod1-corpus/", include=include)
    jobs = JobStore("data/cache/prod1_async/jobs")

    job_id = submit_ingestion(
        adapter, ingest_graph, jobs, job_id="prod1-2d", db=db,
        corpus_ref={"kind": "gcs", "bucket": bucket, "prefix": "prod1-corpus/", "include": sorted(include)},
        is_done=lambda doc: store.contract_by_id(doc.source_doc_id) is not None,
        max_concurrency=3)
    log(f"[2d] submit_ingestion RETURNED IMMEDIATELY -> job_id={job_id} (non-blocking). Polling status ...")

    # poll get_ingestion_status (a plain store read; the exposure interface -- MCP/REST/CLI -- is a product decision)
    last = -1
    while True:
        job = jobs.get(job_id)
        if job.documents_done != last:
            log(f"[2d] status={job.status.value} {job.documents_done}/{job.documents_total} "
                f"(ingested={job.ingested} dead_lettered={len(job.dead_lettered)} partial={len(job.partial)})")
            last = job.documents_done
        if job.done:
            break
        time.sleep(3)

    job = jobs.get(job_id)
    log(f"\n=== PROD-3 2d RESULT ===\nstatus={job.status.value} total={job.documents_total} "
        f"ingested={job.ingested} party_links={job.party_links}")
    log(f"DEAD-LETTERED ({len(job.dead_lettered)}): "
        + "; ".join(f"{d.get('source_doc_id')} [{d.get('stage')}: {d.get('reason')}]" for d in job.dead_lettered))
    log(f"PARTIAL ({len(job.partial)}): "
        + "; ".join(f"{p.get('source_doc_id')} ({len(p.get('clause_failures', []))})" for p in job.partial))
    succeeded = job.status.value == "succeeded" and len(job.dead_lettered) >= 1
    log(f"\n[2d] {'PASS' if succeeded else 'CHECK'}: async submit->parallel->status-poll->completion works; the "
        f"forced-failure doc is {'DEAD-LETTERED (visible on the job, not silent)' if job.dead_lettered else 'NOT dead-lettered'}.")
    store.close()


if __name__ == "__main__":
    main()
