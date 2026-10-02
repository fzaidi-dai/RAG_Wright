"""EP-API-1 (ADR-0117): the engine API layer's opaque workspace + typed config. The product passes an `EngineConfig`
(backend connection + model aliases + embedding profile) and names a `corpus`; the engine returns an opaque
`WorkspaceHandle` that resolves + caches the store/embedder internally -- the product never imports `ArcadeDBStore`
or `query_embedder` and `ws` exposes no store. Hermetic tests cover config defaults + model-id resolution +
opaqueness; the live test proves a real ArcadeDB round-trip + caching through the handle."""
from __future__ import annotations

import os

import pytest

from rag_wright.api import EngineConfig, StoreConfig, WorkspaceHandle, open_workspace
from rag_wright.models.profiles import ModelRole, model_for


def _cfg(**over):
    store = StoreConfig(host="h", port="1", user="u", password="p")
    return EngineConfig(store=store, **over)


# --- config defaults ---

def test_engine_config_defaults():
    cfg = _cfg()
    assert cfg.store.backend == "arcadedb" and cfg.store.protocol == "http"
    assert cfg.models == {} and cfg.embeddings == {"text": "bge-m3"}


# --- model-id resolution (config override wins; else the profile default) ---

def test_model_id_uses_config_override_then_profile_default():
    h = WorkspaceHandle(store=object(), config=_cfg(models={"general": "acme/my-llm"}), corpus="c")
    assert h.model_id(ModelRole.GENERAL) == "acme/my-llm"                       # config override
    assert h.model_id(ModelRole.STRUCTURED_REASONING) == model_for(ModelRole.STRUCTURED_REASONING)  # default


def test_handle_is_opaque_no_public_store_accessor():
    h = WorkspaceHandle(store=object(), config=_cfg(), corpus="c")
    assert not hasattr(h, "store")        # the product cannot reach the concrete store
    assert hasattr(h, "model_id")         # the public resolver is available


# --- live ArcadeDB: open_workspace resolves a real store + caches ---

_TEST_CORPUS = "ragwright_ws_live"


@pytest.mark.store
def test_open_workspace_resolves_live_store_roundtrips_and_caches():
    from rag_wright.store.seam import KgNode

    cfg = EngineConfig(store=StoreConfig(
        host=os.environ["ARCADEDB_HOST"], port=os.environ["ARCADEDB_PORT"],
        user=os.environ["ARCADEDB_USER"], password=os.environ["ARCADEDB_PASSWORD"],
        protocol=os.getenv("ARCADEDB_PROTOCOL", "http")))

    ws = open_workspace(cfg, corpus=_TEST_CORPUS, reset=True)
    assert not hasattr(ws, "store")                      # opaque; engine-internal access is via _store
    ws._store.ensure_compliance_schema()
    ws._store.kg_write([KgNode("Requirement", "requirement_id", {
        "requirement_id": "r1", "source": "S", "citation": "c", "deontic_type": "obligation", "actor": "a",
        "requirement_text": "t", "evidence_standard": "", "severity": "", "applicability_json": [],
        "confidence": "EXTRACTED", "pages": [1], "bbox": None})])
    assert ws._store.kg_read("Requirement", fields=["requirement_id"], where={"requirement_id": "r1"})

    ws2 = open_workspace(cfg, corpus=_TEST_CORPUS)         # same (host,port,corpus) -> cached handle
    assert ws2 is ws
