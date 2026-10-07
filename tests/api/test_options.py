"""EP-API-4a (ADR-0117) + ING-8d: the options catalog on EngineConfig + its threading through the ingestion adapter.
A product tunes ingest through `EngineConfig.options`, not environment variables. The generic `IngestOptions` holds
only engine knobs (`tuning`); a domain pack's own knobs travel in `EngineOptions.packs[<pack name>]` (ING-8d), so
the engine never names a domain field."""
from __future__ import annotations

import asyncio
import dataclasses

from rag_wright.api import EngineConfig, EngineOptions, IngestOptions, StoreConfig, WorkspaceHandle
from rag_wright.api import invoke as _invoke
from rag_wright.packs.contracts.options import ContractIngestOptions


def _store_cfg():
    return StoreConfig(host="h", port="1", user="u", password="p")


def test_generic_ingest_options_hold_only_engine_knobs():
    assert [f.name for f in dataclasses.fields(IngestOptions)] == ["tuning"]
    opts = EngineConfig(store=_store_cfg()).options
    assert opts.ingest.tuning is None
    assert dict(opts.packs) == {}  # no pack configured -> each pack uses its own defaults


def test_contract_pack_options_default_to_none_behavior_neutral():
    opts = ContractIngestOptions()
    assert opts.classify_concurrency is None and opts.clause_concurrency is None
    assert opts.affiliations is None and opts.function_classifier is None


def test_ingest_adapter_threads_the_pack_options_into_the_pipeline(monkeypatch):
    captured: dict = {}

    class _FakeGraph:
        async def ainvoke(self, state):
            return {}

    def _fake_ingest(store, **kw):
        captured.update(kw)
        return _FakeGraph()

    monkeypatch.setattr(
        "rag_wright.packs.contracts.subgraphs.contract_ingestion_pipeline.aproduction_document_ingest", _fake_ingest)
    cfg = EngineConfig(store=_store_cfg(), options=EngineOptions(packs={"contracts": ContractIngestOptions(
        classify_concurrency=4, clause_concurrency=5, affiliations=False, function_classifier="llm")}))
    ws = WorkspaceHandle(store=object(), config=cfg, corpus="c")

    asyncio.run(_invoke.ainvoke_subgraph(
        "contract_ingestion_pipeline", {"document": object(), "cache_dir": "/tmp/x"}, resources=ws))

    assert captured["classify_concurrency"] == 4 and captured["clause_concurrency"] == 5
    assert captured["affiliations"] is False and captured["function_classifier"] == "llm"
    assert "list_model" not in captured and "samples" not in captured  # ING-8d: the dead knobs are gone
    assert captured["embedding_profile"] == "bge-m3"  # EP-API-4b: default profile passed through


def test_ingest_adapter_passes_the_configured_embedding_profile(monkeypatch):
    captured: dict = {}

    class _FakeGraph:
        async def ainvoke(self, state):
            return {}

    monkeypatch.setattr(
        "rag_wright.packs.contracts.subgraphs.contract_ingestion_pipeline.aproduction_document_ingest",
        lambda store, **kw: captured.update(kw) or _FakeGraph())
    cfg = EngineConfig(store=_store_cfg(), embeddings={"text": "my-profile"})
    ws = WorkspaceHandle(store=object(), config=cfg, corpus="c")

    asyncio.run(_invoke.ainvoke_subgraph(
        "contract_ingestion_pipeline", {"document": object(), "cache_dir": "/tmp/x"}, resources=ws))
    assert captured["embedding_profile"] == "my-profile"  # the ingest embedder follows EngineConfig.embeddings


def test_unset_pack_options_pass_none_so_the_pipeline_keeps_its_defaults(monkeypatch):
    captured: dict = {}

    class _FakeGraph:
        async def ainvoke(self, state):
            return {}

    monkeypatch.setattr(
        "rag_wright.packs.contracts.subgraphs.contract_ingestion_pipeline.aproduction_document_ingest",
        lambda store, **kw: captured.update(kw) or _FakeGraph())
    ws = WorkspaceHandle(store=object(), config=EngineConfig(store=_store_cfg()), corpus="c")

    asyncio.run(_invoke.ainvoke_subgraph(
        "contract_ingestion_pipeline", {"document": object(), "cache_dir": "/tmp/x"}, resources=ws))

    # no pack options -> all None -> the pipeline falls back to its env/defaults (behavior-neutral)
    assert captured["classify_concurrency"] is None and captured["affiliations"] is None
    assert captured["function_classifier"] is None and captured["clause_concurrency"] is None
