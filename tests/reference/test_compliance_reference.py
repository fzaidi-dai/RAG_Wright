"""EP-REF-1c (ADR-0118): the GENERIC compliance leg is invocable by name, and the thin reference wrappers show a
product the call shape. Hermetic tests prove the impl_ref caps resolve, the `ainvoke` factories dispatch the right
run fn (text vs document; sections vs document ingest) off the workspace handle, and the wrappers invoke by name.
The `-m store -m model` test runs the leg FULLY LIVE over a workspace: ingest a tiny policy (OpenRouter) -> check a
subject -> a real `ComplianceReport`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import rag_wright.packs.compliance.invokers as ref
from rag_wright.capabilities.invoke import capability_impl
from rag_wright.packs.compliance.subgraphs import compliance_check as cc
from rag_wright.packs.compliance.subgraphs import compliance_ingestion as ci


# --- hermetic: the caps resolve + the factories dispatch + the wrappers invoke by name ----------


def test_compliance_caps_resolve_to_their_ainvoke_factories():
    assert capability_impl("compliance_check") is cc.ainvoke
    assert capability_impl("compliance_ingestion") is ci.ainvoke


class _Handle:
    """A stand-in workspace handle: a store, an embedder, and a role->id resolver (what the factories read)."""

    def __init__(self):
        self._store = object()
        self._embedder = object()

    def model_id(self, role):
        return "stub-model"


async def test_check_factory_dispatches_text_vs_document(monkeypatch):
    calls = {}

    async def _text(*a, **k):
        calls.update(fn="text", args=a, kw=k)
        return "text-report"

    async def _doc(*a, **k):
        calls.update(fn="doc", args=a, kw=k)
        return "doc-report"

    monkeypatch.setattr(cc, "run_generic_compliance_verdict", _text)
    monkeypatch.setattr(cc, "run_compliance_document_verdict", _doc)

    assert await cc.ainvoke(_Handle(), {"subject_text": "ad copy", "source_doc": "ad-1", "sources": ["P"]}) == "text-report"
    assert calls["fn"] == "text" and calls["args"] == ("ad copy", "ad-1")
    assert calls["kw"]["judge_model_id"] == "stub-model" and calls["kw"]["sources"] == ["P"]

    assert await cc.ainvoke(_Handle(), {"doc_name": "f.pdf", "data": b"%PDF"}) == "doc-report"
    assert calls["fn"] == "doc" and calls["args"] == ("f.pdf", b"%PDF")


async def test_ingest_factory_dispatches_sections_vs_document(monkeypatch):
    calls = {}

    async def _sections(*a, **k):
        calls.update(fn="sections", args=a, kw=k)
        return "sec-report"

    async def _doc(*a, **k):
        calls.update(fn="doc", args=a, kw=k)
        return "doc-report"

    monkeypatch.setattr(ci, "run_compliance_ingestion", _sections)
    monkeypatch.setattr(ci, "run_compliance_document_ingestion", _doc)
    monkeypatch.setattr(ci, "default_extraction_model", lambda *a, **k: "EXTRACT_MODEL", raising=False)

    assert await ci.ainvoke(_Handle(), {"source": "P", "sections_path": "/tmp/s.json"}) == "sec-report"
    assert calls["fn"] == "sections" and calls["args"][0] == "/tmp/s.json" and calls["kw"]["source"] == "P"

    assert await ci.ainvoke(_Handle(), {"source": "P", "doc_name": "p.pdf", "data": b"x"}) == "doc-report"
    assert calls["fn"] == "doc" and calls["args"][:2] == ("p.pdf", b"x")


async def test_wrappers_invoke_the_right_capability_by_name(monkeypatch):
    seen = []

    async def _fake(name, inputs, *, resources):
        seen.append((name, inputs))
        return "report"

    monkeypatch.setattr(ref, "ainvoke_subgraph", _fake)
    ws = object()
    await ref.invoke_compliance_check(ws, subject_text="t", source_doc="d")
    await ref.invoke_document_check(ws, doc_name="f.pdf", data=b"x")
    await ref.invoke_policy_ingest(ws, source="P", sections_path="/tmp/s.json")
    await ref.invoke_policy_ingest(ws, source="P", doc_name="p.pdf", data=b"y")

    assert [n for n, _ in seen] == ["compliance_check", "compliance_check", "compliance_ingestion", "compliance_ingestion"]
    assert seen[0][1]["subject_text"] == "t" and "data" not in seen[0][1]
    assert seen[1][1]["data"] == b"x"
    assert seen[2][1]["sections_path"] == "/tmp/s.json" and "data" not in seen[2][1]
    assert seen[3][1]["data"] == b"y"


# --- live (OpenRouter + ArcadeDB): the reference leg end-to-end over a workspace -----------------


def _cfg():
    from rag_wright.api import EngineConfig, StoreConfig
    return EngineConfig(store=StoreConfig(
        host=os.environ["ARCADEDB_HOST"], port=os.environ["ARCADEDB_PORT"],
        user=os.environ["ARCADEDB_USER"], password=os.environ["ARCADEDB_PASSWORD"],
        protocol=os.getenv("ARCADEDB_PROTOCOL", "http")))


def _sections_file(tmp_path: Path) -> Path:
    # two OPERATIVE sections (deontic "must" -> the adapter keeps them; definitions would be skipped)
    p = tmp_path / "policy.sections.json"
    p.write_text(json.dumps([
        {"section": "1.1", "heading": "§ 1.1 Honesty.", "text": "An endorsement must reflect the honest opinion of the endorser."},
        {"section": "1.2", "heading": "§ 1.2 Disclosure.", "text": "A material connection between the endorser and the advertiser must be clearly disclosed."},
    ]), encoding="utf-8")
    return p


@pytest.mark.store
@pytest.mark.model
async def test_compliance_reference_leg_end_to_end_live(tmp_path):
    from rag_wright.api import open_workspace
    from rag_wright.packs.compliance.capabilities.compliance_store import ComplianceStore
    from rag_wright.packs.compliance.schemas.compliance import ComplianceReport

    ws = open_workspace(_cfg(), corpus="ragwright_ref_compliance_live", reset=True)
    source = "DEMO POLICY"

    # Step: ingest a tiny policy through the reference wrapper (real extraction via OpenRouter)
    ingest_report = await ref.invoke_policy_ingest(ws, source=source, sections_path=str(_sections_file(tmp_path)))
    assert ingest_report.documents_ingested == 2
    assert ComplianceStore(ws._store).curated_requirement_count([source]) >= 1  # requirements really landed

    # Step: check a subject that plainly relates to the disclosure rule (real retrieval + judging)
    report = await ref.invoke_compliance_check(
        ws, subject_text="A paid influencer posted a glowing review of the product without disclosing the sponsorship.",
        source_doc="subject-1", sources=[source])
    assert isinstance(report, ComplianceReport)
    assert report.gap_matrix  # the applicable requirement(s) were consulted + rolled up (verdict not pinned -- LLM)
