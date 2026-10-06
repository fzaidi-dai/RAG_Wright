"""Capability discovery (`rag_wright.api.discover`): embedding-ranked selection over the live ARD catalog.

Hermetic tests use a fake keyword-embedder (deterministic one-hot vectors) + a temp catalog, so ranking is
predictable with no model/network. One opt-in `-m embed` test checks real BGE-M3 ranking over the reference pack.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from rag_wright.api import Discovered, discover
from rag_wright.capabilities import manifests as m
from rag_wright.capabilities.manifests import CapabilityManifest


@pytest.fixture
def catalog():
    """Isolate MANIFEST_SPECS: snapshot, clear, yield, restore."""
    saved = dict(m.MANIFEST_SPECS)
    m.MANIFEST_SPECS.clear()
    try:
        yield m
    finally:
        m.MANIFEST_SPECS.clear()
        m.MANIFEST_SPECS.update(saved)


class _FakeEmbedder:
    """One-hot over keyword dims, so cosine ranks the capability whose text shares the query's keyword."""

    _KW = ("retriev", "ingest", "complian")

    def encode_batch(self, texts):
        dense = [[1.0 if kw in t.lower() else 0.0 for kw in self._KW] for t in texts]
        return dense, [{} for _ in texts]


def _ws(embedder=None):
    return SimpleNamespace(_embedder=embedder)


def _register(catalog, slug, kind, queries):
    catalog.register_capability(CapabilityManifest(
        slug=slug, kind=kind, display_name=slug, description=f"{slug} capability",
        representative_queries=tuple(queries)))


def test_ranks_the_semantically_closest_capability_first(catalog):
    _register(catalog, "demo_retrieval", "subgraph", ["retrieve relevant passages", "find spans"])
    _register(catalog, "demo_ingest", "subgraph", ["ingest a document into the graph"])
    out = discover("retrieve passages about a topic", resources=_ws(_FakeEmbedder()))
    assert [d.slug for d in out][0] == "demo_retrieval"
    by = {d.slug: d.score for d in out}
    assert by["demo_retrieval"] > by["demo_ingest"]
    assert isinstance(out[0], Discovered) and out[0].representative_queries  # carries the ranking signal back


def test_kind_filter_and_k_limit(catalog):
    _register(catalog, "demo_retrieval", "subgraph", ["retrieve passages"])
    _register(catalog, "demo_decider", "model", ["retrieve: is this a match"])
    only_models = discover("retrieve", resources=_ws(_FakeEmbedder()), kind="model")
    assert [d.slug for d in only_models] == ["demo_decider"]
    assert len(discover("retrieve", resources=_ws(_FakeEmbedder()), k=1)) == 1


def test_empty_catalog_returns_empty(catalog):
    assert discover("anything", resources=_ws(_FakeEmbedder())) == []


def test_no_embedder_raises_actionable_error(catalog):
    _register(catalog, "demo_retrieval", "subgraph", ["retrieve passages"])
    with pytest.raises(RuntimeError, match="query embedder"):
        discover("retrieve", resources=_ws(None))


@pytest.mark.embed
def test_live_embedding_ranks_a_retrieval_capability_for_a_retrieval_query():
    """Opt-in: real BGE-M3 over the reference pack — a retrieval-shaped query surfaces a retrieval capability."""
    import os

    from rag_wright.api import EngineConfig, StoreConfig, load_reference_pack, open_workspace

    load_reference_pack()
    cfg = EngineConfig(store=StoreConfig(
        host=os.environ.get("ARCADEDB_HOST", "localhost"), port=os.environ.get("ARCADEDB_PORT", "2480"),
        user=os.environ.get("ARCADEDB_USER", "root"), password=os.environ["ARCADEDB_PASSWORD"],
        protocol=os.environ.get("ARCADEDB_PROTOCOL", "http")))
    ws = open_workspace(cfg, corpus="quickstart_demo")
    out = discover("find and retrieve relevant passages from a document", resources=ws, k=5)
    assert out, "discovery returned nothing"
    assert any("retrieval" in d.slug or "qa" in d.slug for d in out[:5]), [d.slug for d in out]
