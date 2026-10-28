"""PS-55 (TexWright, 2026-10-25): Metal is not safe for concurrent use, so the engine keeps one model family on it
(the BGE embedder and reranker, every call under one process-wide lock) and puts Docling and the pack classifiers on
CPU where Metal would otherwise be chosen. Explicit choices win. Hermetic: the device probes are monkeypatched."""
from __future__ import annotations

import concurrent.futures
import threading
import time

import pytest

from rag_wright.util import devices


@pytest.fixture
def metal_host(monkeypatch):
    monkeypatch.setattr(devices, "metal_available", lambda: True)
    monkeypatch.setattr(devices, "_cuda_available", lambda: False)
    for var in ("EMBED_DEVICE", "DOCLING_DEVICE", "RAG_SETFIT_DEVICE"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def cuda_host(monkeypatch):
    monkeypatch.setattr(devices, "metal_available", lambda: False)
    monkeypatch.setattr(devices, "_cuda_available", lambda: True)
    for var in ("EMBED_DEVICE", "DOCLING_DEVICE", "RAG_SETFIT_DEVICE"):
        monkeypatch.delenv(var, raising=False)


def test_on_a_metal_host_only_the_bge_family_gets_metal(metal_host):
    assert devices.torch_device(metal=True) == "mps"
    assert devices.torch_device() == "cpu"
    assert devices.docling_device() == "cpu"


def test_explicit_choices_win_metal_included(metal_host, monkeypatch):
    assert devices.torch_device("cpu", metal=True) == "cpu"
    assert devices.torch_device("mps") == "mps"
    monkeypatch.setenv("EMBED_DEVICE", "cpu")
    monkeypatch.setenv("DOCLING_DEVICE", "mps")
    monkeypatch.setenv("RAG_SETFIT_DEVICE", "mps")
    assert devices.torch_device(env="EMBED_DEVICE", metal=True) == "cpu"
    assert devices.docling_device() == "mps"
    assert devices.torch_device(env="RAG_SETFIT_DEVICE") == "mps"


def test_a_cuda_host_is_unchanged(cuda_host):
    assert devices.torch_device(metal=True) == "cuda"
    assert devices.torch_device() == "cuda"
    assert devices.docling_device() == "auto"  # Docling's own choice


class _SharedProbe:
    """Fake models that all count into ONE in-flight counter, so overlap ACROSS instances is visible."""

    lock = threading.Lock()
    in_flight = 0
    max_in_flight = 0

    @classmethod
    def reset(cls):
        cls.in_flight = cls.max_in_flight = 0

    def _enter(self):
        cls = type(self)
        with cls.lock:
            cls.in_flight += 1
            cls.max_in_flight = max(cls.max_in_flight, cls.in_flight)
        time.sleep(0.003)
        with cls.lock:
            cls.in_flight -= 1

    def encode(self, texts, **_kw):
        import numpy as np

        from rag_wright.contracts.chunk import BGE_M3_DENSE_DIM

        self._enter()
        return {"dense_vecs": [np.zeros(BGE_M3_DENSE_DIM) for _ in texts], "lexical_weights": [{"1": 0.5} for _ in texts]}

    def predict(self, pairs, **_kw):
        self._enter()
        return [0.5 for _ in pairs]


def _hammer(models) -> int:
    _SharedProbe.reset()
    calls = []
    for m in models:
        if hasattr(m, "encode_batch"):
            calls += [lambda m=m: m.encode_batch(["a", "b"])] * 20
        else:
            calls += [lambda m=m: m.score("q", ["p"])] * 20
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for f in [ex.submit(c) for c in calls]:
            f.result()
    return _SharedProbe.max_in_flight


def test_on_metal_two_embedders_and_a_reranker_never_run_at_once(metal_host):
    from rag_wright.capabilities.embedding import BGEM3Embedder
    from rag_wright.capabilities.reranking import BGEReranker

    models = [BGEM3Embedder(model=_SharedProbe()), BGEM3Embedder(model=_SharedProbe()),
              BGEReranker(model=_SharedProbe())]
    assert _hammer(models) == 1


def test_off_metal_separate_instances_still_run_concurrently(metal_host):
    from rag_wright.capabilities.embedding import BGEM3Embedder

    models = [BGEM3Embedder(model=_SharedProbe(), device="cpu"), BGEM3Embedder(model=_SharedProbe(), device="cpu")]
    assert _hammer(models) == 2  # each instance keeps its own lock (issue 0016); no process-wide serialisation


def _docling_devices(converter) -> dict:
    from docling.datamodel.base_models import InputFormat

    return {fmt: converter.format_to_options[fmt].pipeline_options.accelerator_options.device
            for fmt in (InputFormat.PDF, InputFormat.IMAGE)}


def test_docling_runs_on_cpu_on_a_metal_host(metal_host):
    from rag_wright.capabilities.parsing import _new_converter

    assert set(_docling_devices(_new_converter()).values()) == {"cpu"}


def test_docling_device_is_honoured(metal_host, monkeypatch):
    from rag_wright.capabilities.parsing import _new_converter

    monkeypatch.setenv("DOCLING_DEVICE", "mps")
    assert set(_docling_devices(_new_converter()).values()) == {"mps"}


def test_the_pack_classifiers_stay_off_metal(metal_host, monkeypatch):
    from rag_wright.packs.contracts.spans.dim_classifier import auto_device

    assert auto_device(None) == "cpu"
    assert auto_device("mps") == "mps"
    monkeypatch.setenv("RAG_SETFIT_DEVICE", "mps")
    assert auto_device(None) == "mps"
