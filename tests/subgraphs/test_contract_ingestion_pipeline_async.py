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
