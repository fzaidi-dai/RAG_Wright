"""ASYNC-B2e step 1 (ADR-0057): the async per-document ingest graph (`abuild_document_ingest`) + the async
driver (`arun_corpus_ingestion`). Hermetic -- async fake stage fns, no model or store.
"""
from __future__ import annotations

from langgraph.types import RetryPolicy

from rag_wright.subgraphs.contract_ingestion_pipeline import (
    IngestionReport,
    SourceDocument,
    abuild_document_ingest,
    arun_corpus_ingestion,
)

_FAST_RETRY = RetryPolicy(max_attempts=3, initial_interval=0.0)


def _astub_stages(*, fail_chunk_for=frozenset(), fail_graph_for=frozenset(), fail_index_for=frozenset(),
                  fail_write_for=frozenset(), clause_partial_for=None, index_partial_for=None):
    calls: dict = {k: [] for k in ("chunk", "segment", "clauses", "index", "graph", "write")}
    calls["resolve"] = 0
    clause_partial_for = clause_partial_for or {}
    index_partial_for = index_partial_for or {}

    async def chunk_fn(doc):
        calls["chunk"].append(doc.source_doc_id)
        if doc.source_doc_id in fail_chunk_for:
            raise RuntimeError("chunk blip")
        return [f"chunk::{doc.source_doc_id}"]

    async def segment_fn(doc, _chunks):
        calls["segment"].append(doc.source_doc_id)
        return [f"seg::{doc.source_doc_id}"]

    async def clauses_fn(doc, _segments):
        calls["clauses"].append(doc.source_doc_id)
        if doc.source_doc_id in clause_partial_for:  # PROD-3: records + per-clause failures -> PARTIAL
            return {"clause_records": [f"clause::{doc.source_doc_id}"],
                    "clause_failures": clause_partial_for[doc.source_doc_id]}
        return [f"clause::{doc.source_doc_id}"]

    async def index_fn(doc, segments):
        calls["index"].append(doc.source_doc_id)
        if doc.source_doc_id in fail_index_for:
            raise RuntimeError("index blip")
        if doc.source_doc_id in index_partial_for:  # 0006-C: some spans fail to write -> surfaced, not swallowed
            failures = index_partial_for[doc.source_doc_id]
            return {"span_count": len(segments) - len(failures), "span_failures": failures}
        return len(segments)

    async def graph_fn(doc, _chunks):
        calls["graph"].append(doc.source_doc_id)
        if doc.source_doc_id in fail_graph_for:
            from rag_wright.capabilities.dg_extraction import ExtractionFailed

            raise ExtractionFailed("party", "Invalid JSON response: Unterminated string")
        return [f"extraction::{doc.source_doc_id}"]

    async def resolve_fn(extraction_results):
        calls["resolve"] += 1
        return {"resolved": list(extraction_results)}

    async def write_fn(doc, clause_records, resolution):
        calls["write"].append(doc.source_doc_id)
        if doc.source_doc_id in fail_write_for:
            raise RuntimeError("write blip")
        return {"clauses": len(clause_records), "entities": len(resolution["resolved"])}

    return (chunk_fn, segment_fn, clauses_fn, index_fn, graph_fn, resolve_fn, write_fn), calls


class _FakeAdapter:
    def __init__(self, ids):
        self._ids = ids

    def documents(self):
        for i in self._ids:
            yield SourceDocument(source_doc_id=i, text=f"text of {i}")


def _agraph(stages):
    return abuild_document_ingest(*stages, retry_policy=_FAST_RETRY)


async def test_async_ingests_a_document_through_all_stages():
    stages, calls = _astub_stages()
    out = await _agraph(stages).ainvoke({"document": SourceDocument(source_doc_id="C1", text="t")})
    assert calls["chunk"] == ["C1"] and calls["segment"] == ["C1"]
    assert calls["clauses"] == ["C1"] and calls["index"] == ["C1"] and calls["graph"] == ["C1"]  # fan-out
    assert calls["resolve"] == 1
    assert out["written"] == {"clauses": 1, "entities": 1, "spans": 1}
    assert "dead_letter" not in out


async def test_async_bad_document_dead_letters_without_raising():
    stages, _ = _astub_stages(fail_chunk_for={"C1"})
    out = await _agraph(stages).ainvoke({"document": SourceDocument(source_doc_id="C1", text="t")})
    assert out["dead_letter"]["reason"] == "ingest_failed"
    assert out["dead_letter"]["source_doc_id"] == "C1"


async def test_async_index_failure_is_best_effort_and_does_not_dead_letter():
    stages, _ = _astub_stages(fail_index_for={"C1"})
    out = await _agraph(stages).ainvoke({"document": SourceDocument(source_doc_id="C1", text="t")})
    assert out["written"]["spans"] == 0  # the retrieval index degrades to 0...
    assert "dead_letter" not in out  # ...without dead-lettering the document's KG


async def test_async_write_failure_dead_letters_instead_of_crashing():
    stages, _ = _astub_stages(fail_write_for={"C1"})
    out = await _agraph(stages).ainvoke({"document": SourceDocument(source_doc_id="C1", text="t")})
    assert out["dead_letter"]["stage"] == "write" and out["dead_letter"]["source_doc_id"] == "C1"


async def test_async_clause_partial_failure_flags_the_document_not_dead_letter():
    stages, _ = _astub_stages(clause_partial_for={"C1": [{"span_id": "s1", "reason": "trunc"}]})
    report = await arun_corpus_ingestion(
        _FakeAdapter(["C1"]), _agraph(stages), progress=lambda _m: None)
    assert report.documents_ingested == 1 and report.dead_lettered == []
    assert report.partial == [{"source_doc_id": "C1", "clause_failures": [{"span_id": "s1", "reason": "trunc"}]}]


async def test_async_span_write_failures_surface_as_partial_not_swallowed():
    # 0006-C (NFR-2): a per-span index-write failure must be SURFACED (PARTIAL + reason), never silently dropped.
    # The span index is best-effort -- the doc is still ingested (not dead-lettered) -- but the loss is visible.
    fails = [{"span_id": "C1#3", "reason": "SQL newline"}, {"span_id": "C1#7", "reason": "bad vector"}]
    stages, _ = _astub_stages(index_partial_for={"C1": fails})
    report = await arun_corpus_ingestion(
        _FakeAdapter(["C1"]), _agraph(stages), progress=lambda _m: None)
    assert report.documents_ingested == 1 and report.dead_lettered == []  # best-effort: not dead-lettered
    assert report.partial == [{"source_doc_id": "C1", "span_failures": fails}]  # but SURFACED, not swallowed


async def test_async_ingest_graph_threads_span_failures_into_state():
    # the graph node must propagate span_failures out of state (so the driver can report it)
    fails = [{"span_id": "C1#1", "reason": "boom"}]
    stages, _ = _astub_stages(index_partial_for={"C1": fails})
    out = await _agraph(stages).ainvoke({"document": SourceDocument(source_doc_id="C1", text="t")})
    assert out.get("span_failures") == fails
    assert out["written"]["spans"] == 0  # 1 segment, 1 failed -> 0 written


async def test_arun_corpus_ingestion_maps_all_and_dead_letters_one():
    stages, _ = _astub_stages(fail_graph_for={"BAD"})
    report = await arun_corpus_ingestion(
        _FakeAdapter(["C1", "BAD", "C2"]), _agraph(stages), progress=lambda _m: None)
    assert isinstance(report, IngestionReport)
    assert report.documents_ingested == 2
    assert [d["source_doc_id"] for d in report.dead_lettered] == ["BAD"]


async def test_arun_corpus_ingestion_resume_skips_already_done():
    stages, calls = _astub_stages()
    report = await arun_corpus_ingestion(
        _FakeAdapter(["C1", "C2"]), _agraph(stages), progress=lambda _m: None,
        is_done=lambda doc: doc.source_doc_id == "C1")  # C1 already ingested
    assert report.documents_ingested == 2  # both counted present...
    assert calls["chunk"] == ["C2"]  # ...but only C2 was re-processed (C1 resume-skipped)
