"""PROD-3 (ADR-0050): async, job-based ingestion -- submit returns a job_id immediately, the corpus ingests in
the background with bounded document parallelism, and progress is POLLED via a status read (never a blocking run
with a spinner).

This module holds the durable job model + store (2a). The async runner (2b) and the submit/status MCP tools (2c)
build on it. The per-document pipeline stays the existing LangGraph subgraph (with its RetryPolicy +
Increment-1 dead-letter/partial); this is the thin async envelope around `run_corpus_ingestion`'s work.

The JobStore is file-based (one <job_id>.json per job): process-independent, so a status reader in another
process (an MCP call) sees live progress; durable, so a crashed/restarted runner's job record survives; and it
needs no KG schema change for the MVP. The full version can move the store to ArcadeDB/Postgres (ADR-0050).
"""
from __future__ import annotations

import asyncio
import os
import threading
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field

from rag_wright.packs.contracts.subgraphs.contract_ingestion_pipeline import (  # ENG-1 shape + 0009 deferred parse
    PendingDocument,
    aparse_pending,
    build_partial_entry,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStatus(str, Enum):
    QUEUED = "queued"      # submitted, not yet started
    RUNNING = "running"    # the background runner is processing documents
    SUCCEEDED = "succeeded"  # all documents accounted for (ingested / partial / dead-lettered), runner finished
    FAILED = "failed"      # the runner itself errored irrecoverably (NOT a per-document failure -> that's dead_lettered)


class IngestionJob(BaseModel):
    """A durable ingestion job. `dead_lettered` / `partial` carry the PROD-3 lossless outcomes (a failure is
    KNOWN here at completion, never grep-only). Progress = `documents_done` / `documents_total`."""

    job_id: str
    corpus_ref: dict                      # e.g. {"kind": "gcs", "bucket": ..., "prefix": ..., "include": [...]}
    db: str                               # the target KG database
    status: JobStatus = JobStatus.QUEUED
    documents_total: int = 0
    documents_done: int = 0               # ingested (incl. partial) + dead-lettered + resume-skipped
    ingested: int = 0
    dead_lettered: list[dict] = Field(default_factory=list)
    partial: list[dict] = Field(default_factory=list)
    party_links: int = 0
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    error: str | None = None              # a runner-level (not per-document) failure

    @property
    def done(self) -> bool:
        return self.status in (JobStatus.SUCCEEDED, JobStatus.FAILED)


def _atomic_write(path: Path, text: str) -> None:
    """Write `text` to `path` atomically: write a temp file, then `os.replace` (an atomic rename on POSIX and
    Windows). A concurrent reader therefore sees either the old complete file or the new complete one -- never a
    truncated/empty file mid-write (the JobStore read-mid-write race, ADR-0057 B2e/B4)."""
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


class JobStore:
    """File-based job store: one `<job_id>.json` per job under `jobs_dir`. Read-modify-write `update` is safe for
    a SINGLE writer per job (the runner's orchestrator coroutine updates the record; parallel document workers
    report back to it, they do not write the file) -- so no cross-writer race in the MVP."""

    def __init__(self, jobs_dir: Any) -> None:
        self._dir = Path(jobs_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, job_id: str) -> Path:
        return self._dir / f"{job_id}.json"

    def create(self, job: IngestionJob) -> IngestionJob:
        _atomic_write(self._path(job.job_id), job.model_dump_json(indent=2))
        return job

    def get(self, job_id: str) -> IngestionJob | None:
        path = self._path(job_id)
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8")
        # atomic writes (create) mean a reader never sees a partial file; tolerate an empty read defensively
        # (e.g. a truncated legacy write) as "not ready yet" rather than raising.
        return IngestionJob.model_validate_json(text) if text.strip() else None

    def update(self, job_id: str, **fields: Any) -> IngestionJob:
        """Read-modify-write the job's fields, stamping `updated_at`. Raises KeyError if the job is unknown."""
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        updated = job.model_copy(update={**fields, "updated_at": _now()})
        return self.create(updated)

    def list_jobs(self) -> list[IngestionJob]:
        return [IngestionJob.model_validate_json(p.read_text(encoding="utf-8")) for p in sorted(self._dir.glob("*.json"))]


# --- 2b: the async runner (submit returns immediately; a background thread ingests in parallel) ----------------


async def run_job(
    job_id: str,
    documents: list,
    ingest_graph: Any,
    store: JobStore,
    *,
    link_fn: Callable[[], int] = lambda: 0,
    is_done: Callable[[Any], bool] = lambda _doc: False,
    max_concurrency: int = 8,
) -> IngestionJob:
    """Ingest a materialized document list in the background with bounded parallelism, updating the job record
    as each document completes (so status polling sees live progress). Per-document dead-letter / partial (the
    Increment-1 lossless outcomes) are accumulated onto the job. A per-document failure NEVER fails the job -- it
    is dead_lettered; only a runner-level error sets status=FAILED. Updates come from THIS single orchestrator
    coroutine (asyncio is single-threaded; `store.update` has no await), so there is no cross-writer file race."""
    try:
        store.update(job_id, status=JobStatus.RUNNING, documents_total=len(documents))
        sem = asyncio.Semaphore(max_concurrency)
        dead_lettered: list[dict] = []
        partial: list[dict] = []
        done = 0
        ingested = 0

        async def _one(item: Any) -> tuple[str, Any, Any]:
            async with sem:  # bound concurrent LLM/DB + PARSE work (rate limits)
                if is_done(item):  # RESUME: a prior run already wrote this document (skip before parsing)
                    return ("skip", item, None)
                doc = item
                try:
                    # 0009-ASYNC-INGEST: parse a deferred doc HERE -- concurrently + deadline-bounded, off the loop
                    # (its tiered OCR/VLM escalation is the slowest call) -- so it never blocks the others.
                    if isinstance(item, PendingDocument):
                        doc = await aparse_pending(item)
                    # ASYNC-B2e (ADR-0057): ainvoke runs an async-node graph on the loop (true deadline) and a
                    # sync-node graph in LangGraph's threadpool -- so it is safe on any compiled graph.
                    out = await ingest_graph.ainvoke({"document": doc})  # per-doc LangGraph graph
                except Exception as exc:  # noqa: BLE001 - a per-doc CRASH/parse-timeout must dead-letter THAT doc,
                    out = {"dead_letter": {                                          # never fail the whole job
                        "source_doc_id": item.source_doc_id, "stage": "invoke",
                        "reason": "ingest_crashed", "error": str(exc)[:200]}}
                return ("out", doc, out)

        for coro in asyncio.as_completed([_one(doc) for doc in documents]):
            kind, doc, out = await coro
            done += 1
            if kind == "out" and out.get("dead_letter"):
                dead_lettered.append(out["dead_letter"])
            else:
                ingested += 1
                ocr_failures = [{"page": pg, "reason": "unreadable scan (OCR + VLM failed)"}  # 0009-WIRE2
                                for pg in (getattr(doc, "ocr_unreadable_pages", None) or [])]
                entry = build_partial_entry(  # ENG-1: same shape as the blocking driver; also surfaces span/ocr losses
                    doc.source_doc_id, (out or {}).get("clause_failures"), (out or {}).get("span_failures"),
                    ocr_failures, (out or {}).get("graph_failures"))
                if entry is not None:
                    partial.append(entry)
            store.update(job_id, documents_done=done, ingested=ingested,
                         dead_lettered=dead_lettered, partial=partial)

        links = link_fn()  # KG-7 party linking ONCE, after all documents
        return store.update(job_id, status=JobStatus.SUCCEEDED, party_links=links)
    except Exception as exc:  # noqa: BLE001 - a RUNNER-level failure (not a per-document one) -> job FAILED, visible
        return store.update(job_id, status=JobStatus.FAILED, error=str(exc)[:500])


def submit_ingestion(
    adapter: Any,
    ingest_graph: Any,
    store: JobStore,
    *,
    job_id: str,
    db: str,
    corpus_ref: dict,
    link_fn: Callable[[], int] = lambda: 0,
    is_done: Callable[[Any], bool] = lambda _doc: False,
    max_concurrency: int = 8,
) -> str:
    """Create a QUEUED job, start the background runner, and return the `job_id` IMMEDIATELY (non-blocking). The
    runner ingests in a daemon thread with its own event loop; poll `store.get(job_id)` (or the status MCP tool)
    for progress. NOTE (MVP): the daemon thread lives with the submitting process -- a persistent service/worker
    (or LangGraph Platform / Pub-Sub, the 'full' version) is what survives process exit; documented in ADR-0050."""
    store.create(IngestionJob(job_id=job_id, corpus_ref=corpus_ref, db=db))

    def _worker() -> None:
        documents = list(adapter.documents())  # materialize inside the worker (may hit the network)
        asyncio.run(run_job(job_id, documents, ingest_graph, store,
                            link_fn=link_fn, is_done=is_done, max_concurrency=max_concurrency))

    threading.Thread(target=_worker, daemon=True, name=f"ingest-{job_id}").start()
    return job_id
