"""LG-3d: the `contract_ingestion_pipeline` composite -- hermetic (stub stages + fake adapter, no LLM/DB).

The GENERIC ingestion pipeline (corpus-agnostic): chunk -> [extract_clauses || extract_graph] -> resolve ->
write, per document, with a per-document dead-letter so one bad document never kills the corpus ingest. The
corpus driver maps a `CorpusAdapter`'s documents through the pipeline then runs party_clause_linking once.
Adding a corpus = writing one adapter, never re-implementing the flow.
"""

from __future__ import annotations

from langgraph.types import RetryPolicy

from rag_wright.subgraphs.contract_ingestion_pipeline import (
    IngestionReport,
    SourceDocument,
    build_document_ingest,
    run_corpus_ingestion,
)

_FAST_RETRY = RetryPolicy(max_attempts=2, initial_interval=0.0)


def _stub_stages(*, fail_chunk_for=()):
    calls = {"chunk": [], "clauses": [], "graph": [], "resolve": 0, "write": []}

    def chunk_fn(doc):
        calls["chunk"].append(doc.source_doc_id)
        if doc.source_doc_id in fail_chunk_for:
            raise RuntimeError("chunk blip")
        return [f"{doc.source_doc_id}::chunk0"]

    def clauses_fn(doc, chunks):
        calls["clauses"].append(doc.source_doc_id)
        return [f"clause::{doc.source_doc_id}"]

    def graph_fn(doc, chunks):
        calls["graph"].append(doc.source_doc_id)
        return [f"extraction::{doc.source_doc_id}"]

    def resolve_fn(extraction_results):
        calls["resolve"] += 1
        return {"resolved": list(extraction_results)}

    def write_fn(doc, clause_records, resolution):
        calls["write"].append(doc.source_doc_id)
        return {"clauses": len(clause_records), "entities": len(resolution["resolved"])}

    return (chunk_fn, clauses_fn, graph_fn, resolve_fn, write_fn), calls


class _FakeAdapter:
    def __init__(self, ids):
        self._ids = ids

    def documents(self):
        for i in self._ids:
            yield SourceDocument(source_doc_id=i, text=f"text of {i}")


def _graph(stages):
    return build_document_ingest(*stages, retry_policy=_FAST_RETRY)


def test_ingests_a_document_through_all_stages_in_order():
    stages, calls = _stub_stages()
    out = _graph(stages).invoke({"document": SourceDocument(source_doc_id="C1", text="t")})

    assert calls["chunk"] == ["C1"]
    assert calls["clauses"] == ["C1"] and calls["graph"] == ["C1"]  # both extraction paths ran
    assert calls["resolve"] == 1
    assert out["written"] == {"clauses": 1, "entities": 1}
    assert "dead_letter" not in out


def test_bad_document_dead_letters_without_raising():
    stages, calls = _stub_stages(fail_chunk_for={"C1"})
    out = _graph(stages).invoke({"document": SourceDocument(source_doc_id="C1", text="t")})

    assert out["dead_letter"]["reason"] == "ingest_failed"
    assert out["dead_letter"]["source_doc_id"] == "C1"
    assert "written" not in out  # downstream stages skipped
    assert calls["write"] == []


def test_run_corpus_ingestion_maps_all_docs_and_links_once():
    stages, calls = _stub_stages(fail_chunk_for={"BAD"})
    link_calls = {"n": 0}

    def link_fn():
        link_calls["n"] += 1
        return 7  # e.g. PARTY_TO edges written

    report = run_corpus_ingestion(_FakeAdapter(["C1", "BAD", "C2"]), _graph(stages), link_fn=link_fn)

    assert isinstance(report, IngestionReport)
    assert report.documents_ingested == 2  # C1 + C2
    assert [d["source_doc_id"] for d in report.dead_lettered] == ["BAD"]
    assert report.party_links == 7
    assert link_calls["n"] == 1  # link runs ONCE, after all documents
    assert calls["write"] == ["C1", "C2"]  # BAD never reached write


def test_registers_as_a_subgraph():
    from rag_wright.capabilities.registry import CapabilityRegistry
    from rag_wright.subgraphs.contract_ingestion_pipeline import register_contract_ingestion_pipeline

    reg = CapabilityRegistry()
    register_contract_ingestion_pipeline(reg)
    assert reg.get("contract_ingestion_pipeline").kind == "subgraph"
    assert reg.get("contract_ingestion_pipeline").contract is IngestionReport
