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
import threading
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field


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
        self._path(job.job_id).write_text(job.model_dump_json(indent=2), encoding="utf-8")
        return job

    def get(self, job_id: str) -> IngestionJob | None:
        path = self._path(job_id)
        return IngestionJob.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else None

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

        async def _one(doc: Any) -> tuple[str, Any, Any]:
            async with sem:  # bound concurrent LLM/DB work (rate limits)
                if is_done(doc):  # RESUME: a prior run already wrote this document
                    return ("skip", doc, None)
                out = await asyncio.to_thread(ingest_graph.invoke, {"document": doc})  # the per-doc LangGraph graph
                return ("out", doc, out)

        for coro in asyncio.as_completed([_one(doc) for doc in documents]):
            kind, doc, out = await coro
            done += 1
            if kind == "out" and out.get("dead_letter"):
                dead_lettered.append(out["dead_letter"])
            else:
                ingested += 1
                clause_failures = (out or {}).get("clause_failures") or []
                if clause_failures:
                    partial.append({"source_doc_id": doc.source_doc_id, "clause_failures": clause_failures})
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
