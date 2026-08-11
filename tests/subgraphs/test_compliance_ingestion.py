"""CC-5 (compliance §13 C-2): the `compliance_ingestion` subgraph -- regulatory corpus -> Requirement KG.

A hardened LangGraph subgraph on scaffold.py: per § section, requirement_extraction (CC-2) -> write_requirements,
with retry -> dead-letter per section so one bad section never kills the ingest. Reuses the generic
`run_corpus_ingestion` driver + `SourceDocument`/`CorpusAdapter` via a `RegulationAdapter`. Hermetic: stub
extract/write + a fake store, no LLM, no DB.
"""

from __future__ import annotations

import json
from pathlib import Path

from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.subgraphs.compliance_ingestion import (
    RegulationAdapter,
    build_compliance_ingest,
    register_compliance_ingestion,
    run_compliance_ingestion,
)
from rag_wright.subgraphs.contract_ingestion_pipeline import IngestionReport, SourceDocument


def _sections_file(tmp_path: Path) -> Path:
    p = tmp_path / "16cfr255.sections.json"
    p.write_text(json.dumps([
        {"section": "255.1", "heading": "§ 255.1 General.", "text": "Endorsements must reflect honest opinion."},
        {"section": "255.5", "heading": "§ 255.5 Disclosure.", "text": "A material connection must be disclosed."},
    ]), encoding="utf-8")
    return p


# --- RegulationAdapter: one SourceDocument per section -------------------------------------------


def test_adapter_yields_a_document_per_section(tmp_path):
    docs = list(RegulationAdapter(_sections_file(tmp_path), source="FTC 16 CFR 255").documents())
    assert len(docs) == 2
    d = docs[1]
    assert isinstance(d, SourceDocument)
    assert d.metadata["section"] == "255.5" and d.metadata["source"] == "FTC 16 CFR 255"
    assert "material connection" in d.text
    assert "255.5" in d.source_doc_id  # canonical id carries the section


# --- the per-section graph: extract -> write, hardened ------------------------------------------


def _doc() -> SourceDocument:
    return SourceDocument(source_doc_id="ftc_255.5", text="…material connection…",
                          metadata={"section": "255.5", "source": "FTC 16 CFR 255"})


def test_ingest_extracts_then_writes():
    written = {}

    def extract_fn(doc):
        return ["req-a", "req-b"]  # stand-ins; the graph only counts/passes them through

    def write_fn(doc, reqs):
        written["reqs"] = reqs
        return len(reqs)

    graph = build_compliance_ingest(extract_fn, write_fn)
    out = graph.invoke({"document": _doc()})
    assert out.get("dead_letter") is None
    assert out["written"] == {"requirements": 2}
    assert written["reqs"] == ["req-a", "req-b"]


def test_extract_failure_dead_letters_and_skips_write():
    calls = {"write": 0}

    def boom(doc):
        raise RuntimeError("granite down")

    def write_fn(doc, reqs):
        calls["write"] += 1
        return len(reqs)

    graph = build_compliance_ingest(boom, write_fn)
    out = graph.invoke({"document": _doc()})
    assert out.get("dead_letter") and out["dead_letter"]["stage"] == "extract"
    assert calls["write"] == 0  # write never runs on a dead-lettered section


def test_write_failure_dead_letters():
    def extract_fn(doc):
        return ["r"]

    def boom_write(doc, reqs):
        raise RuntimeError("db lock")

    graph = build_compliance_ingest(extract_fn, boom_write)
    out = graph.invoke({"document": _doc()})
    assert out.get("dead_letter") and out["dead_letter"]["stage"] == "write"


# --- the driver: reuses run_corpus_ingestion (fake store + stub extract) -------------------------


class _FakeStore:
    def __init__(self):
        self.reqs = []
        self.schema_ensured = False

    def ensure_compliance_schema(self):
        self.schema_ensured = True

    def write_requirements(self, reqs):
        reqs = list(reqs)
        self.reqs.extend(reqs)
        return len(reqs)


def test_run_over_the_corpus_writes_all_sections(tmp_path):
    store = _FakeStore()
    # inject a stub extractor so no LLM runs: each section -> one requirement stand-in
    report = run_compliance_ingestion(
        _sections_file(tmp_path), store, model=None, source="FTC 16 CFR 255",
        extract_override=lambda doc: [f"req::{doc.metadata['section']}"])
    assert isinstance(report, IngestionReport)
    assert report.documents_ingested == 2 and report.dead_lettered == []
    assert store.schema_ensured is True and store.reqs == ["req::255.1", "req::255.5"]


def test_registers_as_a_subgraph():
    reg = CapabilityRegistry()
    register_compliance_ingestion(reg)
    entry = reg.get("compliance_ingestion")
    assert entry.kind == "subgraph" and entry.contract is IngestionReport


def test_adapter_skips_definitions_sections(tmp_path):
    import json
    p = tmp_path / "reg.sections.json"
    p.write_text(json.dumps([
        {"section": "255.0", "heading": "§ 255.0 Purpose and definitions.", "text": "Endorsement means any..."},
        {"section": "255.5", "heading": "§ 255.5 Disclosure of material connections.", "text": "Must disclose."},
    ]), encoding="utf-8")
    secs = [d.metadata["section"] for d in RegulationAdapter(p, source="FTC 16 CFR 255").documents()]
    assert secs == ["255.5"]  # the definitions section (255.0) is skipped; operative rules kept
    # opt out preserves it
    secs2 = [d.metadata["section"] for d in RegulationAdapter(p, source="x", skip_definitions=False).documents()]
    assert secs2 == ["255.0", "255.5"]


# --- DOCPARSE-1: DocumentRegulationAdapter -- a customer's OWN document (PDF/DOCX) -> sections -> SourceDocuments


def test_document_regulation_adapter_splits_a_parsed_doc_into_section_documents():
    from rag_wright.subgraphs.compliance_ingestion import DocumentRegulationAdapter

    # inject the docling parse result (hermetic): a policy doc split into sections at its headings
    def _sections_fn(name, data):
        return [
            {"section": "1", "heading": "1. Data Retention", "text": "Records must be kept for seven years."},
            {"section": "2", "heading": "2. Definitions", "text": "Personal Data means ..."},  # skipped
            {"section": "3", "heading": "3. Access", "text": "Access is logged and audited."},
        ]

    adapter = DocumentRegulationAdapter("policy.pdf", b"%PDF...", "ACME Privacy Policy", sections_fn=_sections_fn)
    docs = list(adapter.documents())
    assert [d.metadata["section"] for d in docs] == ["1", "3"]  # the Definitions section is skipped
    assert docs[0].text == "Records must be kept for seven years."
    assert docs[0].metadata["source"] == "ACME Privacy Policy"
    assert docs[0].source_doc_id != docs[1].source_doc_id  # distinct canonical ids per section


def test_document_regulation_adapter_skips_empty_sections():
    from rag_wright.subgraphs.compliance_ingestion import DocumentRegulationAdapter

    adapter = DocumentRegulationAdapter(
        "p.pdf", b"x", "src",
        sections_fn=lambda n, d: [{"section": "1", "heading": "H", "text": "  "},
                                  {"section": "2", "heading": "H2", "text": "real body"}])
    assert [d.metadata["section"] for d in adapter.documents()] == ["2"]


def test_run_compliance_document_ingestion_parses_a_doc_and_writes_requirements():
    # DOCPARSE-1 PROD-2 Phase-2: a customer's OWN policy DOCUMENT -> DocumentRegulationAdapter -> the SAME pipeline
    from rag_wright.subgraphs.compliance_ingestion import run_compliance_document_ingestion

    store = _FakeStore()

    def _sections_fn(name, data):  # inject the docling parse (hermetic)
        return [
            {"section": "1", "heading": "1. Retention", "text": "Records kept seven years."},
            {"section": "2", "heading": "2. Definitions", "text": "PII means ..."},   # skipped
            {"section": "3", "heading": "3. Access", "text": "Access is audited."},
        ]

    report = run_compliance_document_ingestion(
        "acme_privacy.pdf", b"%PDF...", store, model=None, source="ACME Privacy Policy",
        sections_fn=_sections_fn, extract_override=lambda doc: [f"req::{doc.metadata['section']}"])
    assert report.documents_ingested == 2 and report.dead_lettered == []  # section 2 (Definitions) skipped
    assert store.schema_ensured is True and store.reqs == ["req::1", "req::3"]
