"""PS-21 (2): the engine owns ingestion's fan-out, so it checks it against a model server's ceiling.

`aingest` runs up to `min(document_concurrency, documents) x extract_concurrency` extractor calls at once. When a
model the workspace resolves carries a `max_concurrency` (a deployed server registers its `max_num_seqs` there) and
that peak can exceed it, `aingest` warns before it starts: the warning names the peak, the model and its ceiling."""
from __future__ import annotations

import asyncio

import pytest

from rag_wright.api import EngineConfig, IngestionTuning, ModelRole, StoreConfig, build_ingestion
from rag_wright.api.workspace import WorkspaceHandle
from rag_wright.models.profiles import PROFILES, ModelProfile, register_model_profile

from tests.ingestion.test_builder import FIXTURES, _FakeEmbedder, _FakeStore, _record_extractor

DOCS = [str(FIXTURES / "textile_spec_sheet.md")] * 3


@pytest.fixture
def server_model():
    saved = dict(PROFILES)
    register_model_profile(ModelProfile(model_id="qwen@acme", backend="vllm", base_url="https://x/v1",
                                        max_concurrency=12))
    yield "qwen@acme"
    PROFILES.clear()
    PROFILES.update(saved)


def _ingest(tmp_path, model, tuning, docs=DOCS):
    ws = WorkspaceHandle(_FakeStore(), EngineConfig(store=StoreConfig(host="x", port="0", user="u", password="p"),
                                                    models={ModelRole.GENERAL.value: model}), "test")
    lines = []
    pipe = build_ingestion(_record_extractor, embedder=_FakeEmbedder(), progress=lines.append, tuning=tuning)
    asyncio.run(pipe.aingest(ws, docs, cache_dir=tmp_path / "cache"))
    return lines


def test_a_fan_out_above_the_ceiling_warns(tmp_path, server_model):
    with pytest.warns(RuntimeWarning, match=r"16 extractor calls.*qwen@acme.*12"):
        lines = _ingest(tmp_path, server_model, IngestionTuning(document_concurrency=2, extract_concurrency=8))
    assert any("qwen@acme" in line and "12" in line for line in lines)  # in the monitored progress log too


def test_a_fan_out_within_the_ceiling_is_quiet(tmp_path, server_model, recwarn):
    _ingest(tmp_path, server_model, IngestionTuning(document_concurrency=2, extract_concurrency=6))
    assert not [w for w in recwarn if "extractor calls" in str(w.message)]


def test_the_peak_counts_only_the_documents_present(tmp_path, server_model, recwarn):
    # one document: the peak is extract_concurrency (8), not document_concurrency x extract_concurrency (32)
    _ingest(tmp_path, server_model, IngestionTuning(document_concurrency=4, extract_concurrency=8), docs=DOCS[:1])
    assert not [w for w in recwarn if "extractor calls" in str(w.message)]


def test_a_model_with_no_known_ceiling_is_quiet(tmp_path, recwarn):
    _ingest(tmp_path, "some-provider-model", IngestionTuning(document_concurrency=8, extract_concurrency=32))
    assert not [w for w in recwarn if "extractor calls" in str(w.message)]
