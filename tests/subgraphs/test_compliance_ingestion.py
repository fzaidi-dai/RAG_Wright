"""CC-5 (compliance §13 C-2): the `compliance_ingestion` subgraph -- regulatory corpus -> Requirement KG.

A hardened LangGraph subgraph on scaffold.py: per § section, requirement_extraction (CC-2) -> write_requirements,
with retry -> dead-letter per section so one bad section never kills the ingest. Reuses the generic
`run_corpus_ingestion` driver + `SourceDocument`/`CorpusAdapter` via a `RegulationAdapter`. Hermetic: stub
extract/write + a fake store, no LLM, no DB.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.compliance_fakes import FakeRequirementSeam

from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.packs.compliance.subgraphs.compliance_ingestion import (
    RegulationAdapter,
    build_compliance_ingest,
    register_compliance_ingestion,
    run_compliance_ingestion,
)
from rag_wright.packs.contracts.subgraphs.contract_ingestion_pipeline import IngestionReport, SourceDocument


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


async def test_ingest_extracts_then_writes():
    written = {}

    async def extract_fn(doc):
        return ["req-a", "req-b"]  # stand-ins; the graph only counts/passes them through

    async def write_fn(doc, reqs):
        written["reqs"] = reqs
        return len(reqs)

    graph = build_compliance_ingest(extract_fn, write_fn)
    out = await graph.ainvoke({"document": _doc()})
    assert out.get("dead_letter") is None
    assert out["written"] == {"requirements": 2}
    assert written["reqs"] == ["req-a", "req-b"]


async def test_extract_failure_dead_letters_and_skips_write():
    calls = {"write": 0}

    async def boom(doc):
        raise RuntimeError("granite down")

    async def write_fn(doc, reqs):
        calls["write"] += 1
        return len(reqs)

    graph = build_compliance_ingest(boom, write_fn)
    out = await graph.ainvoke({"document": _doc()})
    assert out.get("dead_letter") and out["dead_letter"]["stage"] == "extract"
    assert calls["write"] == 0  # write never runs on a dead-lettered section


async def test_write_failure_dead_letters():
    async def extract_fn(doc):
        return ["r"]

    async def boom_write(doc, reqs):
        raise RuntimeError("db lock")

    graph = build_compliance_ingest(extract_fn, boom_write)
    out = await graph.ainvoke({"document": _doc()})
    assert out.get("dead_letter") and out["dead_letter"]["stage"] == "write"


# --- the driver: reuses run_corpus_ingestion (fake store + stub extract) -------------------------


class _FakeStore(FakeRequirementSeam):
    """The generic seam the compliance pack reads through `ComplianceStore` (ING-8e), plus a test-local
    `write_requirements` the tests inject as the write override."""

    def __init__(self):
        super().__init__()
        self.reqs = []
        self._done_citations: set[str] = set()

    @property
    def schema_ensured(self) -> bool:
        return bool(self.packs)  # ComplianceStore ensured the pack schema from compliance_bridge.ttl

    def write_requirements(self, reqs):
        reqs = list(reqs)
        self.reqs.extend(reqs)
        return len(reqs)

    def kg_read(self, node_type, *, where=None, fields=None, distinct=None, order_by=None, limit=None):
        if distinct == "citation":  # the resume read: citations already ingested for this source
            return [{"citation": c} for c in sorted(self._done_citations)]
        return super().kg_read(node_type, where=where, fields=fields, distinct=distinct)


async def test_run_over_the_corpus_writes_all_sections(tmp_path):
    store = _FakeStore()

    async def _ov(doc):  # inject a stub extractor so no LLM runs: each section -> one requirement stand-in
        return [f"req::{doc.metadata['section']}"]

    report = await run_compliance_ingestion(
        _sections_file(tmp_path), store, model=None, source="FTC 16 CFR 255", extract_override=_ov,
        write_override=store.write_requirements)  # DD-1b: write is an injectable seam; the fake records reqs
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


def test_is_operative_is_a_deontic_cue_gate():
    # P3c (Gap 1): operative iff the text carries a deontic CUE (from the ttl), with word boundaries.
    from rag_wright.packs.compliance.subgraphs.compliance_ingestion import is_operative

    assert is_operative("The advertiser must disclose the connection.")       # obligation cue
    assert is_operative("An endorser may not conceal a paid relationship.")   # prohibition cue
    assert is_operative("The licensee may use the mark.")                     # permission cue
    assert not is_operative("Endorsement means any advertising message.")     # definitional -> no cue
    assert not is_operative("This part sets forth the general purpose.")      # purpose -> no cue
    assert not is_operative("Maybe later at the muster point.")               # word-boundary: 'maybe'/'muster' != cue


def test_adapter_gates_by_deontic_cue_not_heading(tmp_path):
    # P3c: the gate is CUE-based, not heading-based -- the INVERSION of the old hack: a "Definitions" heading with
    # an OPERATIVE rule is KEPT (the heading hack wrongly dropped it), and a normal-heading section with NO rule is
    # SKIPPED (the heading hack wrongly kept it).
    import json
    p = tmp_path / "reg.sections.json"
    p.write_text(json.dumps([
        {"section": "1", "heading": "1. Definitions", "text": "The manufacturer must not overstate results."},
        {"section": "2", "heading": "2. Scope", "text": "This policy applies to all product categories."},
    ]), encoding="utf-8")
    secs = [d.metadata["section"] for d in RegulationAdapter(p, source="x").documents()]
    assert secs == ["1"]  # operative rule under a "Definitions" heading KEPT; no-cue "Scope" section SKIPPED


# --- DOCPARSE-1: DocumentRegulationAdapter -- a customer's OWN document (PDF/DOCX) -> sections -> SourceDocuments


def test_document_regulation_adapter_splits_a_parsed_doc_into_section_documents():
    from rag_wright.packs.compliance.subgraphs.compliance_ingestion import DocumentRegulationAdapter

    # inject the docling parse result (hermetic): a policy doc split into sections at its headings
    def _sections_fn(name, data):
        return [
            {"section": "1", "heading": "1. Data Retention", "text": "Records must be kept for seven years."},
            {"section": "2", "heading": "2. Definitions", "text": "Personal Data means ..."},  # skipped
            {"section": "3", "heading": "3. Access", "text": "Access must be logged and audited."},
        ]

    adapter = DocumentRegulationAdapter("policy.pdf", b"%PDF...", "ACME Privacy Policy", sections_fn=_sections_fn)
    docs = list(adapter.documents())
    assert [d.metadata["section"] for d in docs] == ["1", "3"]  # the Definitions section is skipped
    assert docs[0].text == "Records must be kept for seven years."
    assert docs[0].metadata["source"] == "ACME Privacy Policy"
    assert docs[0].source_doc_id != docs[1].source_doc_id  # distinct canonical ids per section


def test_document_regulation_adapter_skips_empty_sections():
    from rag_wright.packs.compliance.subgraphs.compliance_ingestion import DocumentRegulationAdapter

    adapter = DocumentRegulationAdapter(
        "p.pdf", b"x", "src",
        sections_fn=lambda n, d: [{"section": "1", "heading": "H", "text": "  "},
                                  {"section": "2", "heading": "H2", "text": "The body must comply."}])
    assert [d.metadata["section"] for d in adapter.documents()] == ["2"]


async def test_run_compliance_document_ingestion_parses_a_doc_and_writes_requirements():
    # DOCPARSE-1 PROD-2 Phase-2: a customer's OWN policy DOCUMENT -> DocumentRegulationAdapter -> the SAME pipeline
    from rag_wright.packs.compliance.subgraphs.compliance_ingestion import run_compliance_document_ingestion

    store = _FakeStore()

    def _sections_fn(name, data):  # inject the docling parse (hermetic)
        return [
            {"section": "1", "heading": "1. Retention", "text": "Records must be kept for seven years."},
            {"section": "2", "heading": "2. Definitions", "text": "PII means ..."},   # skipped
            {"section": "3", "heading": "3. Access", "text": "Access must be audited."},
        ]

    async def _ov(doc):
        return [f"req::{doc.metadata['section']}"]

    report = await run_compliance_document_ingestion(
        "acme_privacy.pdf", b"%PDF...", store, model=None, source="ACME Privacy Policy",
        sections_fn=_sections_fn, extract_override=_ov, write_override=store.write_requirements)
    assert report.documents_ingested == 2 and report.dead_lettered == []  # section 2 (Definitions) skipped
    assert store.schema_ensured is True and store.reqs == ["req::1", "req::3"]


# --- COMP-ASYNC-1: lossless (failed section dead-letters) + async envelope --------------------------------------


async def test_failed_section_dead_letters_instead_of_writing_zero_requirements(tmp_path):
    # COMP-ASYNC-1 lossless: an extraction FAILURE (raises) must dead-letter the section, not silently write 0 reqs
    store = _FakeStore()

    async def _boom(doc):
        raise RuntimeError("granite JSON garble")

    report = await run_compliance_ingestion(
        _sections_file(tmp_path), store, model=None, source="FTC 16 CFR 255", extract_override=_boom)
    assert report.documents_ingested == 0            # nothing silently "ingested"
    assert len(report.dead_lettered) == 2            # both sections dead-lettered (visible)
    assert store.reqs == []                           # and NO partial/silent writes
    assert report.dead_lettered[0]["stage"] == "extract"


async def test_run_requirement_extraction_raise_on_failure_surfaces_the_dead_letter():
    from rag_wright.packs.compliance.subgraphs.requirement_extraction import (
        RequirementExtractionFailed,
        run_requirement_extraction,
    )

    async def _boom(_text):
        raise RuntimeError("extract blew up")

    # default (back-compat): swallows -> []
    assert await run_requirement_extraction("t", model=None, source="s", section="1", extract_override=_boom) == []
    # raise_on_failure: surfaces the failure
    with pytest.raises(RequirementExtractionFailed) as ei:
        await run_requirement_extraction("t", model=None, source="s", section="1",
                                         extract_override=_boom, raise_on_failure=True)
    assert ei.value.section == "1"


def test_submit_compliance_ingestion_is_async_and_dead_letters_a_failed_section(tmp_path):
    import time

    from rag_wright.packs.contracts.subgraphs.async_ingestion import JobStore
    from rag_wright.packs.compliance.subgraphs.compliance_ingestion import RegulationAdapter, submit_compliance_ingestion

    store = _FakeStore()
    store.database = "ragwright_compliance_test"
    jobs = JobStore(tmp_path / "jobs")
    adapter = RegulationAdapter(_sections_file(tmp_path), source="FTC 16 CFR 255")

    async def _extract(doc):  # 255.5 fails, 255.1 succeeds
        if doc.metadata["section"] == "255.5":
            raise RuntimeError("boom")
        return [f"req::{doc.metadata['section']}"]

    job_id = submit_compliance_ingestion(
        adapter, store, jobs, job_id="cjob", model=None, source="FTC 16 CFR 255", extract_override=_extract,
        write_override=store.write_requirements)
    assert job_id == "cjob" and jobs.get("cjob") is not None  # returned immediately

    deadline = time.time() + 10
    while time.time() < deadline and not jobs.get("cjob").done:
        time.sleep(0.05)
    job = jobs.get("cjob")
    assert job.status.value == "succeeded"
    assert job.ingested == 1                          # 255.1 succeeded
    assert len(job.dead_lettered) == 1                # 255.5 dead-lettered (visible on the job, not silent)
    assert store.reqs == ["req::255.1"]


async def test_compliance_resume_skips_already_ingested_sections(tmp_path):
    # PROD-2 #2: a section whose citation already has requirements is SKIPPED (not re-extracted) on a re-run
    store = _FakeStore()
    store._done_citations = {"§ 255.1"}  # 255.1 already ingested by a prior run
    extracted = []

    async def _ov(doc):
        extracted.append(doc.metadata["section"])
        return [f"req::{doc.metadata['section']}"]

    report = await run_compliance_ingestion(
        _sections_file(tmp_path), store, model=None, source="FTC 16 CFR 255", extract_override=_ov,
        write_override=store.write_requirements)  # DD-1b: write is an injectable seam; the fake records reqs
    assert report.documents_ingested == 2               # both counted present...
    assert extracted == ["255.5"]                        # ...but 255.1 was resume-skipped, only 255.5 extracted
    assert store.reqs == ["req::255.5"]


async def test_compliance_no_resume_when_nothing_ingested_yet(tmp_path):
    store = _FakeStore()  # empty done-set -> all sections run
    extracted = []

    async def _ov(doc):
        extracted.append(doc.metadata["section"])
        return []

    await run_compliance_ingestion(
        _sections_file(tmp_path), store, model=None, source="FTC 16 CFR 255", extract_override=_ov,
        write_override=store.write_requirements)
    assert sorted(extracted) == ["255.1", "255.5"]
