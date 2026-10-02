"""EP-API-4a (ADR-0117): the ingest options catalog on EngineConfig + its threading through the ingestion adapter.
A product tunes ingest through `EngineConfig.options.ingest`, not environment variables."""
from __future__ import annotations

import asyncio

from rag_wright.api import EngineConfig, EngineOptions, IngestOptions, StoreConfig, WorkspaceHandle
from rag_wright.api import invoke as _invoke


def _store_cfg():
    return StoreConfig(host="h", port="1", user="u", password="p")


def test_ingest_options_default_to_none_behavior_neutral():
    opts = EngineConfig(store=_store_cfg()).options.ingest
    assert opts.classify_concurrency is None and opts.clause_concurrency is None
    assert opts.affiliations is None and opts.function_classifier is None
    assert opts.list_model is None and opts.clause_samples is None


def test_ingest_adapter_threads_the_options_into_the_pipeline(monkeypatch):
    captured: dict = {}

    class _FakeGraph:
        async def ainvoke(self, state):
            return {}

    def _fake_ingest(store, **kw):
        captured.update(kw)
        return _FakeGraph()

    monkeypatch.setattr(
        "rag_wright.subgraphs.contract_ingestion_pipeline.aproduction_document_ingest", _fake_ingest)
    cfg = EngineConfig(store=_store_cfg(), options=EngineOptions(ingest=IngestOptions(
        classify_concurrency=4, clause_concurrency=5, affiliations=False, function_classifier="llm",
        list_model="off", clause_samples=3)))
    ws = WorkspaceHandle(store=object(), config=cfg, corpus="c")

    asyncio.run(_invoke.ainvoke_subgraph(
        "contract_ingestion_pipeline", {"document": object(), "cache_dir": "/tmp/x"}, resources=ws))

    assert captured["classify_concurrency"] == 4 and captured["clause_concurrency"] == 5
    assert captured["affiliations"] is False and captured["function_classifier"] == "llm"
    assert captured["list_model"] == "off" and captured["samples"] == 3


def test_unset_options_pass_none_so_the_pipeline_keeps_its_defaults(monkeypatch):
    captured: dict = {}

    class _FakeGraph:
        async def ainvoke(self, state):
            return {}

    monkeypatch.setattr(
        "rag_wright.subgraphs.contract_ingestion_pipeline.aproduction_document_ingest",
        lambda store, **kw: captured.update(kw) or _FakeGraph())
    ws = WorkspaceHandle(store=object(), config=EngineConfig(store=_store_cfg()), corpus="c")

    asyncio.run(_invoke.ainvoke_subgraph(
        "contract_ingestion_pipeline", {"document": object(), "cache_dir": "/tmp/x"}, resources=ws))

    # all None -> the pipeline falls back to its env/defaults (behavior-neutral for a caller who sets nothing)
    assert captured["classify_concurrency"] is None and captured["affiliations"] is None
    assert captured["function_classifier"] is None and captured["samples"] is None
