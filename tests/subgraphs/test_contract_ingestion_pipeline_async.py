"""ASYNC-B2c (ADR-0057): async per-contract graph extraction + the async party-extract fn. Hermetic --
injected async fns, no model.
"""
from __future__ import annotations

import json

import rag_wright.capabilities.dg_extraction as dg
from rag_wright.capabilities.graph_extraction import aproduction_extract_fn
from rag_wright.subgraphs.contract_ingestion_pipeline import (
    SourceDocument,
    aper_contract_graph_extraction,
)


async def test_aper_contract_graph_extracts_once_then_caches(tmp_path):
    party_dir = tmp_path / "graph_parties"
    calls = {"n": 0}

    async def _aextract(_text):
        calls["n"] += 1
        return ["Acme Co."]

    doc = SourceDocument(source_doc_id="C2", text="body")
    first = await aper_contract_graph_extraction(doc, party_dir=party_dir, anames_fn=_aextract)
    second = await aper_contract_graph_extraction(doc, party_dir=party_dir, anames_fn=_aextract)
    assert calls["n"] == 1  # extracted ONCE; the second call served the freshly-written cache
    assert [m.text for m in first[0].entity_mentions] == ["Acme Co."]
    assert len(first) == len(second) == 1


async def test_aper_contract_graph_appends_affiliations_and_caches_them_separately(tmp_path):
    # issue 0027: when the affiliation fn is wired, AFFILIATE_OF facts are appended alongside CONTRACTS_WITH,
    # cached in a SEPARATE dir, and not re-extracted on re-ingest.
    from rag_wright.contracts.ontology import RelationshipType

    party_dir = tmp_path / "graph_parties"
    affil_dir = tmp_path / "graph_affiliations"
    calls = {"party": 0, "affil": 0}

    async def _aparties(_text):
        calls["party"] += 1
        return ["Acme Holdings Ltd", "Northwind Trading Ltd"]

    async def _aaffil(_text):
        calls["affil"] += 1
        return [("Acme Holdings Ltd", "Acme Corp")]

    doc = SourceDocument(source_doc_id="C3", text="... an affiliate of Acme Corp ...")
    first = await aper_contract_graph_extraction(
        doc, party_dir=party_dir, anames_fn=_aparties, affil_dir=affil_dir, aaffiliations_fn=_aaffil)
    second = await aper_contract_graph_extraction(
        doc, party_dir=party_dir, anames_fn=_aparties, affil_dir=affil_dir, aaffiliations_fn=_aaffil)
    assert calls == {"party": 1, "affil": 1}                       # each extracted once, then cached
    facts = [f for er in first for f in er.relationship_facts]
    kinds = {f.relationship_type for f in facts}
    assert RelationshipType.CONTRACTS_WITH in kinds and RelationshipType.AFFILIATE_OF in kinds
    affil = next(f for f in facts if f.relationship_type is RelationshipType.AFFILIATE_OF)
    assert affil.source_ref == "Acme Holdings Ltd" and affil.target_ref == "Acme Corp"
    assert (affil_dir / "C3.json").exists()                        # separate cache written
    assert len(first) == len(second)


async def test_aper_contract_graph_without_affiliations_is_unchanged(tmp_path):
    # issue 0027: affiliation params omitted -> parties only, exactly as before (backward compatible)
    party_dir = tmp_path / "graph_parties"

    async def _aparties(_text):
        return ["Acme Co."]

    out = await aper_contract_graph_extraction(
        SourceDocument(source_doc_id="C4", text="body"), party_dir=party_dir, anames_fn=_aparties)
    facts = [f for er in out for f in er.relationship_facts]
    assert all(f.relationship_type.value == "Contracts With" for f in facts)  # no AFFILIATE_OF when not wired


async def test_aper_contract_graph_reuses_a_seeded_cache(tmp_path):
    party_dir = tmp_path / "graph_parties"
    party_dir.mkdir()
    (party_dir / "C1.json").write_text(json.dumps(["Acme Co.", "Beta LLC"]))

    async def _must_not(_text):
        raise AssertionError("anames_fn called despite a seeded cache hit")

    out = await aper_contract_graph_extraction(
        SourceDocument(source_doc_id="C1", text="t"), party_dir=party_dir, anames_fn=_must_not)
    assert [m.text for m in out[0].entity_mentions] == ["Acme Co.", "Beta LLC"]


async def test_aper_contract_graph_no_parties_yields_no_extraction(tmp_path):
    async def _none(_t):
        return []

    out = await aper_contract_graph_extraction(
        SourceDocument(source_doc_id="C3", text="t"), party_dir=tmp_path, anames_fn=_none)
    assert out == []


async def test_aproduction_extract_fn_awaits_aextract_parties(monkeypatch):
    async def fake_aextract(text, _model):
        return f"parties:{text}"

    monkeypatch.setattr(dg, "openrouter_model", lambda _label, _mid: "dummy-model")
    monkeypatch.setattr(dg, "aextract_parties", fake_aextract)
    afn = aproduction_extract_fn()
    assert await afn("acme") == "parties:acme"


# --- PARTIAL-CAUSE-1: a TRANSIENT ExtractionFailed is retried and recovers (not lost) -----------

from rag_wright.capabilities.dg_extraction import ExtractionFailed  # noqa: E402
from rag_wright.subgraphs.contract_ingestion_pipeline import _aextract_clause_with_retry  # noqa: E402


class _FlakyExtractor:
    """`aextract` raises ExtractionFailed the first `fail_times` calls (a TRANSIENT docling blip -- empty LLM
    content), then succeeds. docling-graph raises ExtractionFailed on ANY logged error, so a transient must be
    retried, not recorded as a lost clause (PARTIAL-CAUSE-1)."""

    def __init__(self, fail_times: int, record: str = "OK") -> None:
        self.fail_times = fail_times
        self.calls = 0
        self.record = record

    async def aextract(self, *, chunk_id, function, text, span_id, functions=()):  # noqa: ANN001, ARG002
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ExtractionFailed("clause", "LiteLLM returned empty content")  # a transient blip, not furniture
        return self.record


async def test_clause_extract_retries_a_transient_extraction_failed_and_recovers():
    ex = _FlakyExtractor(fail_times=2)  # two transient blips, then success on the third attempt
    record, reason = await _aextract_clause_with_retry(
        ex, chunk_id="c:0:h", function="Payment Terms", text="Net 30 days.", span_id="s", attempts=3)
    assert record == "OK" and reason == "" and ex.calls == 3  # recovered -- NOT turned into a lost clause


async def test_clause_extract_reports_a_persistent_failure_after_exhausting_retries():
    ex = _FlakyExtractor(fail_times=99)  # never succeeds
    record, reason = await _aextract_clause_with_retry(
        ex, chunk_id="c:0:h", function="Payment Terms", text="Net 30 days.", span_id="s", attempts=3)
    assert record is None and "empty content" in reason and ex.calls == 3  # no-silent-loss: recorded as a failure
