"""LG-3d: the `contract_ingestion_pipeline` composite -- hermetic (stub stages + fake adapter, no LLM/DB).

The GENERIC ingestion pipeline (corpus-agnostic): chunk -> [extract_clauses || extract_graph] -> resolve ->
write, per document, with a per-document dead-letter so one bad document never kills the corpus ingest. The
corpus driver maps a `CorpusAdapter`'s documents through the pipeline.
Adding a corpus = writing one adapter, never re-implementing the flow.
"""

from __future__ import annotations

from langgraph.types import RetryPolicy

from rag_wright.subgraphs.contract_ingestion_pipeline import (
    IngestionReport,
    SourceDocument,
    per_contract_graph_extraction,
    seed_chunk_cache,
    seed_party_cache,
)

_FAST_RETRY = RetryPolicy(max_attempts=2, initial_interval=0.0)




















def test_registers_as_a_subgraph():
    from rag_wright.capabilities.registry import CapabilityRegistry
    from rag_wright.subgraphs.contract_ingestion_pipeline import register_contract_ingestion_pipeline

    reg = CapabilityRegistry()
    register_contract_ingestion_pipeline(reg)
    assert reg.get("contract_ingestion_pipeline").kind == "subgraph"
    assert reg.get("contract_ingestion_pipeline").contract is IngestionReport


# --- INGEST-REFACTOR (a): per-contract GP-1B + cache reuse ---------------------------------------------------

def test_seed_party_cache_canonicalizes_keys_and_is_idempotent(tmp_path):
    import json

    legacy = tmp_path / "dg_extracted_parties.json"
    # the legacy key is the RAW title (spaces/commas); the pipeline addresses by canonical source_doc_id (HYG-1)
    legacy.write_text(json.dumps({"ACME CO_01_2020-EX-10-SUPPLY AGREEMENT": ["Acme Co.", "Beta LLC"]}))
    party_dir = tmp_path / "graph_parties"

    assert seed_party_cache(party_dir, legacy) == 1
    seeded = party_dir / "ACME_CO_01_2020-EX-10-SUPPLY_AGREEMENT.json"  # canonicalized: spaces -> underscores
    assert json.loads(seeded.read_text()) == ["Acme Co.", "Beta LLC"]
    assert seed_party_cache(party_dir, legacy) == 0  # idempotent: never re-writes an existing entry


def test_seed_party_cache_missing_legacy_is_noop(tmp_path):
    assert seed_party_cache(tmp_path / "graph_parties", tmp_path / "does_not_exist.json") == 0


def test_seed_chunk_cache_copies_manifests_only_once(tmp_path):
    legacy = tmp_path / "legacy_chunks"
    legacy.mkdir()
    (legacy / "DocA.deadbeefcafe0001.chunks.json").write_text("{}")
    (legacy / "skip.txt").write_text("not a manifest")
    chunk_dir = tmp_path / "chunks"

    assert seed_chunk_cache(chunk_dir, legacy) == 1  # only the .chunks.json manifest
    assert (chunk_dir / "DocA.deadbeefcafe0001.chunks.json").exists()
    assert seed_chunk_cache(chunk_dir, legacy) == 0  # idempotent


def test_per_contract_graph_reuses_seeded_names_without_extracting(tmp_path):
    import json

    party_dir = tmp_path / "graph_parties"
    party_dir.mkdir()
    (party_dir / "C1.json").write_text(json.dumps(["Acme Co.", "Beta LLC"]))

    def _must_not_extract(_text):
        raise AssertionError("names_fn called despite a seeded cache hit")

    out = per_contract_graph_extraction(
        SourceDocument(source_doc_id="C1", text="t"), party_dir=party_dir, names_fn=_must_not_extract)

    assert len(out) == 1  # ONE ExtractionResult per contract (not per chunk)
    result = out[0]
    assert [m.text for m in result.entity_mentions] == ["Acme Co.", "Beta LLC"]
    assert len(result.relationship_facts) == 1  # a CONTRACTS_WITH edge between the two parties
    assert result.chunk_id.source_doc_id == "C1"  # provenance stays on the contract (KG-7 join)


def test_per_contract_graph_extracts_once_then_caches(tmp_path):
    party_dir = tmp_path / "graph_parties"
    calls = {"n": 0}

    def _extract(_text):
        calls["n"] += 1
        return ["Acme Co."]

    doc = SourceDocument(source_doc_id="C2", text="body")
    first = per_contract_graph_extraction(doc, party_dir=party_dir, names_fn=_extract)
    second = per_contract_graph_extraction(doc, party_dir=party_dir, names_fn=_extract)

    assert calls["n"] == 1  # extracted ONCE; the second call served the freshly-written cache
    assert len(first) == len(second) == 1
    assert [m.text for m in first[0].entity_mentions] == ["Acme Co."]
    assert first[0].relationship_facts == []  # a lone party yields a mention but no edge


def test_per_contract_graph_no_parties_yields_no_extraction(tmp_path):
    out = per_contract_graph_extraction(
        SourceDocument(source_doc_id="C3", text="t"), party_dir=tmp_path, names_fn=lambda _t: [])
    assert out == []


# (issue 0028 / ADR-0091: the KG-7 `corpus_party_link_fn` / PartyTo link step was retired; its tests were removed
#  with it. `arun_corpus_ingestion(link_fn=...)` keeps the generic no-op seam, covered by test_async_ingestion.)


# --- PROD-3 lossless invariant (ADR-0050): no silent partial success -------------------------------------------








# --- issue 0033 follow-up: the ingest extraction models are caller-configurable --------------------------------

def test_ingest_extraction_models_are_caller_configurable(monkeypatch, tmp_path):
    """extract_model / list_model / samples on aproduction_document_ingest thread to granite_clause_extractor
    (a bare model-id string is wrapped into an ExtractionModel), so a caller no longer needs env vars to change
    the ingest extraction model or its gemma+granite list-union second model."""
    import pytest

    from rag_wright.subgraphs import contract_ingestion_pipeline as pipe

    captured: dict = {}

    class _StopHere(Exception):
        pass

    def _fake_granite(model=None, *, semantic_judge_fn=None, asemantic_judge_fn=None,
                      list_model=None, samples=None):
        captured.update(model=model, list_model=list_model, samples=samples)
        raise _StopHere  # stop before the rest of the (network-y) wiring

    monkeypatch.setattr("rag_wright.spans.clause_kg_extractor.granite_clause_extractor", _fake_granite)

    with pytest.raises(_StopHere):
        pipe.aproduction_document_ingest(
            store=object(), cache_dir=str(tmp_path), registry=object(), embedder=object(),
            extract_model="some/model-x", list_model="gemma-y", samples=3)
    assert captured["list_model"] == "gemma-y" and captured["samples"] == 3
    assert getattr(captured["model"], "model", None) == "some/model-x"  # bare id -> ExtractionModel

    captured.clear()
    with pytest.raises(_StopHere):
        pipe.aproduction_document_ingest(
            store=object(), cache_dir=str(tmp_path), registry=object(), embedder=object())
    # no args -> backend/env defaults preserved (existing callers unaffected)
    assert captured == {"model": None, "list_model": None, "samples": None}
