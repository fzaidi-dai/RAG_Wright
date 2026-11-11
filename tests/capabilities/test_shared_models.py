"""PS-65 (TexWright, 2026-11-09): every workspace, every ingestion pipeline and every `intra_document_qa` call loaded
its own copy of the same BGE model (~3.5 s and ~300 MB each). A process now loads a model once per (model, precision,
device) and shares it; every wrapper of one model holds that model's ONE lock, so the in-place mutation FlagEmbedding
does on each encode (issue 0016) stays serialized across all of them. Hermetic: the model classes are faked."""
from __future__ import annotations

import sys
import threading
import time
import types

import pytest

from rag_wright.capabilities.embedding import BGEM3Embedder
from rag_wright.capabilities.reranking import BGEReranker
from rag_wright.util import devices


class _FakeModel:
    built = 0

    def __init__(self, *args, **kwargs):
        type(self).built += 1
        time.sleep(0.05)  # a slow load, so concurrent first use overlaps
        self.args, self.kwargs = args, kwargs
        self.model = types.SimpleNamespace(half=lambda: None)


@pytest.fixture
def fake_models(monkeypatch):
    class M3(_FakeModel):
        built = 0

    class CE(_FakeModel):
        built = 0

    monkeypatch.setitem(sys.modules, "FlagEmbedding", types.SimpleNamespace(BGEM3FlagModel=M3))
    monkeypatch.setitem(sys.modules, "sentence_transformers", types.SimpleNamespace(CrossEncoder=CE))
    monkeypatch.setattr(devices, "_SHARED_MODELS", {})
    return M3, CE


def test_two_embedders_share_one_model_and_its_one_lock(fake_models):
    m3, _ = fake_models
    a, b = BGEM3Embedder(device="cpu"), BGEM3Embedder(device="cpu")
    assert m3.built == 1 and a._model is b._model and a._lock is b._lock


def test_a_different_precision_device_or_model_is_its_own_model(fake_models):
    m3, _ = fake_models
    base = BGEM3Embedder(device="cpu")
    others = [BGEM3Embedder(device="cpu", use_fp16=True), BGEM3Embedder(device="cuda"),
              BGEM3Embedder("BAAI/other", device="cpu")]
    assert m3.built == 4 and len({id(e._model) for e in [base, *others]}) == 4
    assert len({id(e._lock) for e in [base, *others]}) == 4


def test_concurrent_first_use_loads_the_model_once(fake_models):
    m3, _ = fake_models
    out: list = []
    threads = [threading.Thread(target=lambda: out.append(BGEM3Embedder(device="cpu"))) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert m3.built == 1 and len({id(e._model) for e in out}) == 1


def test_an_injected_model_is_never_shared(fake_models):
    m3, _ = fake_models
    a, b = BGEM3Embedder(model=object(), device="cpu"), BGEM3Embedder(model=object(), device="cpu")
    assert m3.built == 0 and a._model is not b._model and a._lock is not b._lock


def test_on_metal_every_shared_model_holds_the_process_metal_lock(fake_models):
    assert BGEM3Embedder(device="mps")._lock is devices.METAL_LOCK
    assert BGEReranker(device="mps")._lock is devices.METAL_LOCK


def test_rerankers_share_one_model_too(fake_models):
    _, ce = fake_models
    a, b = BGEReranker(device="cpu"), BGEReranker(device="cpu")
    assert ce.built == 1 and a._model is b._model and a._lock is b._lock
    assert a._lock is not BGEM3Embedder(device="cpu")._lock  # a different model, its own lock off Metal


# --- live -----------------------------------------------------------------------------------------------------------

@pytest.mark.embed
def test_live_two_workspaces_share_one_model_and_concurrent_encodes_stay_exact(monkeypatch):
    # the condition issue 0016 guards: several threads encoding through DIFFERENT wrappers of the one shared model
    from concurrent.futures import ThreadPoolExecutor

    from rag_wright.api import EngineConfig, StoreConfig
    from rag_wright.api.workspace import WorkspaceHandle

    monkeypatch.delenv("STACK_URL", raising=False)
    monkeypatch.setenv("EMBED_DEVICE", "cpu")
    a = WorkspaceHandle(None, EngineConfig(store=StoreConfig(host="x", port="0", user="u", password="p")), "a")
    b = WorkspaceHandle(None, EngineConfig(store=StoreConfig(host="x", port="0", user="u", password="p")), "b")
    ea, eb = a.embedder, b.embedder
    assert ea is not eb and ea._model is eb._model and ea._lock is eb._lock
    texts = [f"warp knitted spacer fabric, sample {i}, 380 GSM" for i in range(24)]
    serial = [ea.encode_dense(t) for t in texts]
    with ThreadPoolExecutor(8) as pool:
        both = list(pool.map(lambda it: (ea if it[0] % 2 else eb).encode_dense(it[1]), enumerate(texts)))
    assert both == serial
