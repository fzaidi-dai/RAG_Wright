"""PS-16: a workspace's configuration change takes effect without a restart. `open_workspace` returns its cached
handle only for an EQUAL config; a different config gets a new handle, reusing the store when the store settings and
the pack are unchanged and the query embedder when the embedding profile is unchanged. A call still holding the old
handle keeps the old config."""
from __future__ import annotations

import pytest

from rag_wright.api import EngineConfig, ModelRole, StoreConfig, open_workspace
from rag_wright.api import workspace as w

STORE = StoreConfig(host="h", port="1", user="u", password="p")


class _Store:
    def __init__(self, corpus, reset):
        self.corpus, self.reset, self.schema_ensured = corpus, reset, 0

    def ensure_schema(self):
        self.schema_ensured += 1


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    built = {"stores": [], "embedders": []}

    def build_store(config, corpus, *, reset):
        built["stores"].append(_Store(corpus, reset))
        return built["stores"][-1]

    def build_embedder(config):
        built["embedders"].append(config.embeddings.get("text"))
        return object()

    monkeypatch.setattr(w, "_build_store", build_store)
    monkeypatch.setattr(w, "_build_embedder", build_embedder)
    monkeypatch.setattr(w, "_WORKSPACES", {})
    return built


def _cfg(**kw):
    return EngineConfig(store=kw.pop("store", STORE), **kw)


def test_an_equal_config_returns_the_cached_handle(fakes):
    first = open_workspace(_cfg(models={"general": "m1"}), corpus="c")
    again = open_workspace(_cfg(models={"general": "m1"}), corpus="c")  # equal, not the same object
    assert again is first and len(fakes["stores"]) == 1


def test_a_changed_config_takes_effect_and_reuses_the_store_and_embedder(fakes):
    old = open_workspace(_cfg(models={"general": "m1"}), corpus="c")
    old._embedder  # built on first use
    new = open_workspace(_cfg(models={"general": "m2"}), corpus="c")
    assert new is not old
    assert new.model_id(ModelRole.GENERAL) == "m2" and old.model_id(ModelRole.GENERAL) == "m1"  # in-flight keeps old
    assert new._store is old._store and len(fakes["stores"]) == 1  # same store settings + pack: no new connection
    assert new._embedder is old._embedder and fakes["embedders"] == ["bge-m3"]  # same profile: not rebuilt
    assert open_workspace(_cfg(models={"general": "m2"}), corpus="c") is new  # the new handle is now the cached one


def test_a_new_embedding_profile_builds_its_own_embedder(fakes):
    old = open_workspace(_cfg(), corpus="c")
    old._embedder
    new = open_workspace(_cfg(embeddings={"text": "other"}), corpus="c")
    new._embedder
    assert new._store is old._store and fakes["embedders"] == ["bge-m3", "other"]


@pytest.mark.parametrize("change", [
    {"store": StoreConfig(host="h", port="1", user="u", password="rotated")},
    {"pack": "my_pack.ttl"},
])
def test_new_store_settings_or_pack_build_a_new_store(fakes, change):
    old = open_workspace(_cfg(), corpus="c")
    new = open_workspace(_cfg(**change), corpus="c")
    assert new._store is not old._store and len(fakes["stores"]) == 2
    assert new._store.schema_ensured == 1  # the new pack's types are ensured


def test_reset_still_drops_and_recreates(fakes):
    first = open_workspace(_cfg(), corpus="c")
    fresh = open_workspace(_cfg(), corpus="c", reset=True)
    assert fresh is not first and fakes["stores"][-1].reset is True
