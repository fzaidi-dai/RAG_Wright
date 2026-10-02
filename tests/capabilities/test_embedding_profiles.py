"""EP-API-4b (ADR-0117): the embedding-profile seam -- alias -> (query, ingest) embedder builders. Hermetic: we
never build the real bge-m3 encoders (heavy); we assert the registry logic + that a profile is pluggable."""
from __future__ import annotations

import pytest

from rag_wright.capabilities import embedding_profiles as ep


def test_bge_m3_is_the_default_profile_in_both_registries():
    assert "bge-m3" in ep._QUERY_BUILDERS and "bge-m3" in ep._INGEST_BUILDERS


def test_unknown_profile_raises_on_both_sides():
    with pytest.raises(ValueError):
        ep.build_query_embedder("nope")
    with pytest.raises(ValueError):
        ep.build_ingest_embedder("nope")


def test_a_registered_profile_is_pluggable(monkeypatch):
    q, i = object(), object()
    monkeypatch.setitem(ep._QUERY_BUILDERS, "stub", lambda: q)
    monkeypatch.setitem(ep._INGEST_BUILDERS, "stub", lambda: i)
    assert ep.build_query_embedder("stub") is q
    assert ep.build_ingest_embedder("stub") is i
