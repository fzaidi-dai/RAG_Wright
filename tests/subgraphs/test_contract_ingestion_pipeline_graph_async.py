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


def _astub_stages(*, fail_chunk_for=frozenset(), fail_graph_for=frozenset()):
    calls: dict = {k: [] for k in ("chunk", "segment", "clauses", "index", "graph", "write")}
    calls["resolve"] = 0

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
        return [f"clause::{doc.source_doc_id}"]

    async def index_fn(doc, segments):
        calls["index"].append(doc.source_doc_id)
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


async def test_arun_corpus_ingestion_maps_all_and_dead_letters_one():
    stages, _ = _astub_stages(fail_graph_for={"BAD"})
    report = await arun_corpus_ingestion(
        _FakeAdapter(["C1", "BAD", "C2"]), _agraph(stages), progress=lambda _m: None)
    assert isinstance(report, IngestionReport)
    assert report.documents_ingested == 2
    assert [d["source_doc_id"] for d in report.dead_lettered] == ["BAD"]
