"""PS-14: every role-based model call honours the workspace's `EngineConfig.models`, not only answer generation and
relevance. Each engine entry point that takes a workspace (ingestion, the invokers) scopes its call to that
workspace's models; `use_workspace_models(ws)` does the same for calls that take no workspace (parsing, a chunk
discoverer). Precedence: the workspace's config, then `RAG_MODEL_<ROLE>`, then `RAG_MODEL_ALL`, then the default.
Two workspaces used concurrently each see their own models."""
from __future__ import annotations

import asyncio

import pytest

from rag_wright.api import (
    CapabilityManifest,
    EngineConfig,
    IngestSource,
    ModelRole,
    StoreConfig,
    UnitExtraction,
    ainvoke_model,
    ainvoke_subgraph,
    build_ingestion,
    invoke_model,
    register_capability,
    use_workspace_models,
)
from rag_wright.api.workspace import WorkspaceHandle
from rag_wright.capabilities import manifests as m
from rag_wright.models.profiles import model_for
from rag_wright.util.concurrent import map_concurrent_async

from tests.ingestion.test_builder import FIXTURES, _FakeEmbedder, _FakeStore


def _ws(models: dict[str, str]) -> WorkspaceHandle:
    cfg = EngineConfig(store=StoreConfig(host="x", port="0", user="u", password="p"), models=models)
    return WorkspaceHandle(_FakeStore(), cfg, "test")


A = _ws({ModelRole.GENERAL.value: "model-a", ModelRole.VISION_OCR.value: "ocr-a"})
B = _ws({ModelRole.GENERAL.value: "model-b"})


@pytest.fixture(autouse=True)
def _no_env_overrides(monkeypatch):
    for role in ModelRole:
        monkeypatch.delenv(f"RAG_MODEL_{role.name}", raising=False)
    monkeypatch.delenv("RAG_MODEL_ALL", raising=False)


def test_a_workspace_scope_resolves_its_models():
    default, summarization = model_for(ModelRole.GENERAL), model_for(ModelRole.SUMMARIZATION)
    with use_workspace_models(A):
        assert model_for(ModelRole.GENERAL) == "model-a"
        assert model_for(ModelRole.VISION_OCR) == "ocr-a"
        assert model_for(ModelRole.SUMMARIZATION) == summarization  # not in A's config: the default applies
    assert model_for(ModelRole.GENERAL) == default


def test_the_workspace_config_wins_over_the_environment(monkeypatch):
    monkeypatch.setenv("RAG_MODEL_GENERAL", "env-model")
    monkeypatch.setenv("RAG_MODEL_ALL", "env-all")
    with use_workspace_models(A):
        assert model_for(ModelRole.GENERAL) == "model-a"
        assert model_for(ModelRole.SUMMARIZATION) == "env-all"  # not in A's config: the environment applies
    assert model_for(ModelRole.GENERAL) == "env-model"


async def test_concurrent_workspaces_each_see_their_own_models():
    async def run(ws):
        with use_workspace_models(ws):
            seen = []
            for _ in range(3):
                await asyncio.sleep(0)  # interleave with the other workspace
                seen.append(model_for(ModelRole.GENERAL))
            return seen

    assert await asyncio.gather(run(A), run(B)) == [["model-a"] * 3, ["model-b"] * 3]


async def test_threads_started_by_the_engine_keep_the_workspace_scope():
    with use_workspace_models(A):
        out = await map_concurrent_async([1, 2], lambda _: model_for(ModelRole.GENERAL), timeout_s=5)
    assert out == ["model-a", "model-a"]


@pytest.fixture
def probes():
    saved = dict(m.MANIFEST_SPECS)

    def manifest(slug, kind, attr):
        return CapabilityManifest(slug=slug, kind=kind, display_name=slug, description="probe",
                                  representative_queries=("probe",), impl_ref=f"{__name__}:{attr}")

    register_capability(manifest("probe_model_sync", "model", "probe_sync"))
    register_capability(manifest("probe_model_async", "model", "probe_async"))
    register_capability(manifest("probe_subgraph", "subgraph", "probe_async"))
    try:
        yield
    finally:
        m.MANIFEST_SPECS.clear()
        m.MANIFEST_SPECS.update(saved)


def probe_sync(resources, inputs):
    return model_for(ModelRole.GENERAL)


async def probe_async(resources, inputs):
    return model_for(ModelRole.GENERAL)


async def test_the_invokers_scope_each_call_to_its_workspace(probes):
    assert invoke_model("probe_model_sync", {}, resources=A) == "model-a"
    assert await ainvoke_model("probe_model_sync", {}, resources=B) == "model-b"
    assert await ainvoke_model("probe_model_async", {}, resources=A) == "model-a"
    assert await ainvoke_subgraph("probe_subgraph", {}, resources=B) == "model-b"
    assert await asyncio.gather(ainvoke_model("probe_model_async", {}, resources=A),
                                ainvoke_model("probe_model_async", {}, resources=B)) == ["model-a", "model-b"]


async def test_ingestion_runs_under_its_workspaces_models(tmp_path):
    seen = []

    async def extractor(unit, *, source_doc_id):
        seen.append(model_for(ModelRole.GENERAL))
        return UnitExtraction()

    pipe = build_ingestion(extractor, embedder=_FakeEmbedder(), progress=lambda _: None)
    await pipe.aingest(B, [IngestSource(path=str(FIXTURES / "textile_spec_sheet.md"))], cache_dir=tmp_path)
    assert seen and set(seen) == {"model-b"}
