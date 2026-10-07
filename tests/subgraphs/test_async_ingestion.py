"""PROD-3 (ADR-0050) Increment 2a: the durable IngestionJob model + file-based JobStore. Hermetic (tmp dir)."""

from __future__ import annotations

import asyncio
import time

import pytest

from rag_wright.packs.contracts.subgraphs.async_ingestion import (
    IngestionJob,
    JobStatus,
    JobStore,
    run_job,
    submit_ingestion,
)
from rag_wright.packs.contracts.subgraphs.contract_ingestion_pipeline import SourceDocument


def _job(**kw) -> IngestionJob:
    base = dict(job_id="job1", corpus_ref={"kind": "gcs", "bucket": "b", "prefix": "p/"}, db="ragwright_prod1")
    base.update(kw)
    return IngestionJob(**base)


def test_job_defaults_are_queued_and_empty():
    j = _job()
    assert j.status is JobStatus.QUEUED
    assert j.documents_done == 0 and j.dead_lettered == [] and j.partial == []
    assert j.created_at and j.updated_at and j.error is None
    assert not j.done


def test_store_create_get_roundtrip(tmp_path):
    store = JobStore(tmp_path)
    store.create(_job(documents_total=25))
    got = store.get("job1")
    assert got is not None and got.documents_total == 25 and got.corpus_ref["bucket"] == "b"


def test_store_get_unknown_is_none(tmp_path):
    assert JobStore(tmp_path).get("nope") is None


def test_store_update_modifies_fields_bumps_updated_at_and_persists(tmp_path):
    store = JobStore(tmp_path)
    created = store.create(_job())
    updated = store.update("job1", status=JobStatus.RUNNING, documents_total=10, documents_done=3)
    assert updated.status is JobStatus.RUNNING and updated.documents_done == 3
    assert updated.updated_at >= created.updated_at
    # persisted: a FRESH store (another process) reads the update
    assert JobStore(tmp_path).get("job1").documents_done == 3


def test_store_update_unknown_raises(tmp_path):
    with pytest.raises(KeyError):
        JobStore(tmp_path).update("nope", status=JobStatus.RUNNING)


def test_done_property_reflects_terminal_status():
    assert _job(status=JobStatus.SUCCEEDED).done
    assert _job(status=JobStatus.FAILED).done
    assert not _job(status=JobStatus.RUNNING).done


def test_list_jobs_returns_all(tmp_path):
    store = JobStore(tmp_path)
    store.create(_job(job_id="a"))
    store.create(_job(job_id="b"))
    assert {j.job_id for j in store.list_jobs()} == {"a", "b"}


# --- 2b: the async runner --------------------------------------------------------------------------------------


class _FakeGraph:
    """A per-document graph stub: returns dead_letter / partial / clean per doc id, mirroring the real out dict."""

    def __init__(self, *, dead_letter_for=(), partial_for=(), span_partial_for=(), raise_for=()):
        self.dl = set(dead_letter_for)
        self.pf = set(partial_for)
        self.spf = set(span_partial_for)
        self.raise_for = set(raise_for)

    def invoke(self, state):
        doc = state["document"]
        if doc.source_doc_id in self.raise_for:
            raise RuntimeError("graph blew up")
        if doc.source_doc_id in self.dl:
            return {"dead_letter": {"source_doc_id": doc.source_doc_id, "stage": "extract_graph", "reason": "boom"}}
        cf = [{"span_id": "s1", "reason": "trunc"}] if doc.source_doc_id in self.pf else []
        sf = [{"span_id": "s9", "reason": "SQL newline"}] if doc.source_doc_id in self.spf else []
        return {"written": {"clauses": 1}, "clause_failures": cf, "span_failures": sf}

    async def ainvoke(self, state):  # run_job now uses ainvoke (ASYNC-B2e); delegate to invoke (patchable in tests)
        return self.invoke(state)


def _docs(*ids):
    return [SourceDocument(source_doc_id=i, text=f"t{i}") for i in ids]


def test_run_job_ingests_all_and_succeeds_with_dead_letter_and_partial(tmp_path):
    store = JobStore(tmp_path)
    store.create(_job())
    graph = _FakeGraph(dead_letter_for={"C2"}, partial_for={"C3"})
    job = asyncio.run(run_job("job1", _docs("C1", "C2", "C3"), graph, store, link_fn=lambda: 5))

    assert job.status is JobStatus.SUCCEEDED
    assert job.documents_total == 3 and job.documents_done == 3
    assert job.ingested == 2  # C1 + C3 (C2 dead-lettered)
    assert [d["source_doc_id"] for d in job.dead_lettered] == ["C2"]
    assert [p["source_doc_id"] for p in job.partial] == ["C3"]
    assert job.party_links == 5


def test_run_job_surfaces_a_span_only_loss_with_unified_failures(tmp_path):
    # ENG-1: the fire-and-forget job path previously read only clause_failures -> a span-only loss vanished into a
    # clean-looking ingest. It must now flag PARTIAL with the same kind-tagged `failures` shape as the blocking driver.
    store = JobStore(tmp_path)
    store.create(_job(job_id="jobspan"))
    graph = _FakeGraph(span_partial_for={"C2"})
    job = asyncio.run(run_job("jobspan", _docs("C1", "C2"), graph, store, link_fn=lambda: 0))

    assert job.status is JobStatus.SUCCEEDED and job.ingested == 2  # span index is best-effort: not dead-lettered
    assert [p["source_doc_id"] for p in job.partial] == ["C2"]      # but the loss IS surfaced
    entry = job.partial[0]
    assert "clause_failures" not in entry                          # the trap: reading only this key misses it
    assert entry["failures"] == [{"kind": "span", "span_id": "s9", "reason": "SQL newline"}]
    assert entry["span_failures"] == [{"span_id": "s9", "reason": "SQL newline"}]


def test_run_job_resume_skips_done_docs(tmp_path):
    store = JobStore(tmp_path)
    store.create(_job())
    calls = []
    graph = _FakeGraph()
    orig = graph.invoke
    graph.invoke = lambda state: (calls.append(state["document"].source_doc_id), orig(state))[1]
    job = asyncio.run(run_job("job1", _docs("C1", "C2"), graph, store,
                              is_done=lambda doc: doc.source_doc_id == "C1"))
    assert job.status is JobStatus.SUCCEEDED and job.documents_done == 2 and job.ingested == 2
    assert calls == ["C2"]  # C1 resume-skipped, only C2 ran through the graph


def test_run_job_runner_level_error_marks_failed(tmp_path):
    store = JobStore(tmp_path)
    store.create(_job())

    def _boom():
        raise RuntimeError("link step exploded")

    job = asyncio.run(run_job("job1", _docs("C1"), _FakeGraph(), store, link_fn=_boom))
    assert job.status is JobStatus.FAILED and "exploded" in job.error


def test_submit_returns_job_id_immediately_then_completes(tmp_path):
    class _Adapter:
        def documents(self):
            return _docs("C1", "C2", "C3")

    store = JobStore(tmp_path)
    job_id = submit_ingestion(_Adapter(), _FakeGraph(partial_for={"C2"}), store,
                              job_id="jobX", db="ragwright_prod1",
                              corpus_ref={"kind": "gcs", "prefix": "p/"})
    assert job_id == "jobX"
    assert store.get("jobX") is not None  # created immediately (non-blocking submit)

    deadline = time.time() + 10
    while time.time() < deadline and not (store.get("jobX") or _job()).done:
        time.sleep(0.05)
    final = store.get("jobX")
    assert final.status is JobStatus.SUCCEEDED and final.documents_done == 3
    assert [p["source_doc_id"] for p in final.partial] == ["C2"]


def test_run_job_per_document_crash_dead_letters_that_doc_not_the_whole_job(tmp_path):
    # a per-doc graph that RAISES (a crash, not a returned dead_letter) must dead-letter THAT doc; the job succeeds
    store = JobStore(tmp_path)
    store.create(_job())
    job = asyncio.run(run_job("job1", _docs("C1", "C2", "C3"), _FakeGraph(raise_for={"C2"}), store))
    assert job.status is JobStatus.SUCCEEDED and job.ingested == 2
    assert [d["source_doc_id"] for d in job.dead_lettered] == ["C2"]
    assert job.dead_lettered[0]["stage"] == "invoke" and "blew up" in job.dead_lettered[0]["error"]


# --- 0009-ASYNC-INGEST: deferred parse runs off-loop, bounded, concurrent ------------------------

async def test_aparse_pending_parses_off_loop_and_merges_metadata():
    from rag_wright.packs.contracts.subgraphs.contract_ingestion_pipeline import PendingDocument, aparse_pending

    def _parse():
        return SourceDocument(source_doc_id="C1", text="parsed", metadata={"a": 1})

    sd = await aparse_pending(PendingDocument("C1", _parse, {"source": "gcs"}))
    assert sd.text == "parsed" and sd.metadata == {"source": "gcs", "a": 1}  # adapter meta merged in


async def test_aparse_pending_is_wall_clock_bounded():
    import time

    import pytest

    from rag_wright.packs.contracts.subgraphs.contract_ingestion_pipeline import PendingDocument, aparse_pending

    def _slow():
        time.sleep(0.5)  # a hung OCR/VLM escalation
        return SourceDocument(source_doc_id="C1", text="x")

    with pytest.raises(TimeoutError):  # the deadline unblocks the caller -> the doc dead-letters, job survives
        await aparse_pending(PendingDocument("C1", _slow, {}), deadline_s=0.02)


def test_run_job_parses_pending_documents(tmp_path):
    # the job path parses deferred docs itself (concurrently) instead of upfront
    from rag_wright.packs.contracts.subgraphs.contract_ingestion_pipeline import PendingDocument

    store = JobStore(tmp_path)
    store.create(_job(job_id="jp"))

    def mk(sid):
        return PendingDocument(sid, lambda sid=sid: SourceDocument(source_doc_id=sid, text=f"t{sid}"), {})

    job = asyncio.run(run_job("jp", [mk("C1"), mk("C2")], _FakeGraph(), store, link_fn=lambda: 0))
    assert job.status is JobStatus.SUCCEEDED and job.ingested == 2


# --- aparsed_source_document: the async-bounded twin of parsed_source_document (for a hand-built ingest) --------

async def test_aparsed_source_document_is_the_async_bounded_twin(monkeypatch):
    import rag_wright.capabilities.document_parse as cip

    seen = {}

    def _stub(sid, name, data, *, cache_dir, metadata=None, include_hidden_sheets, tuning):
        seen.update(sid=sid, name=name, data=data, cache_dir=cache_dir, metadata=metadata,
                    hidden=include_hidden_sheets)
        return SourceDocument(source_doc_id=sid, text="parsed", metadata=metadata or {})

    monkeypatch.setattr(cip, "parsed_source_document", _stub)
    sd = await cip.aparsed_source_document("D1", "d.pdf", b"%PDF", cache_dir="/tmp/x", metadata={"a": 1})
    assert isinstance(sd, SourceDocument) and sd.source_doc_id == "D1" and sd.text == "parsed"   # returns SourceDocument
    assert seen == {"sid": "D1", "name": "d.pdf", "data": b"%PDF", "cache_dir": "/tmp/x", "metadata": {"a": 1},
                    "hidden": True}


async def test_aparsed_source_document_is_wall_clock_bounded(monkeypatch):
    import time

    import rag_wright.capabilities.document_parse as cip

    def _slow(sid, name, data, *, cache_dir, metadata=None, include_hidden_sheets=True, tuning=None):
        time.sleep(0.5)  # a hung OCR/VLM escalation
        return SourceDocument(source_doc_id=sid, text="x")

    monkeypatch.setattr(cip, "parsed_source_document", _slow)
    with pytest.raises(TimeoutError):  # the deadline unblocks the caller off-loop
        await cip.aparsed_source_document("D1", "d.pdf", b"x", cache_dir="/tmp/x", deadline_s=0.02)
