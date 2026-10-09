"""PS-8b (G21): `rag_wright.pack_sdk` is the pack-author tier -- re-exports of the engine's own objects (no copies),
disjoint from `rag_wright.api`, with distinct names where the explicit form would clash with the API's."""
from __future__ import annotations

import pytest

import rag_wright.api as api
import rag_wright.pack_sdk as sdk


def test_every_export_resolves_and_none_duplicates_the_api():
    assert all(hasattr(sdk, n) for n in sdk.__all__)
    assert set(sdk.__all__).isdisjoint(api.__all__)


def test_the_exports_are_the_engine_objects_and_clashing_names_are_renamed():
    from rag_wright.capabilities import answer_generator, span_relevance_judgment
    from rag_wright.contracts import extraction
    from rag_wright.corpus import document_parser
    from rag_wright.models import seam

    assert sdk.agenerate_answer_with_model is answer_generator.agenerate_answer  # api.agenerate_answer takes a ws
    assert sdk.ajudge_spans_with_judge is span_relevance_judgment.ajudge_spans
    assert sdk.GraphExtractor is extraction.Extractor  # api.Extractor is the ingestion hook
    assert sdk.parse_docling_bytes is document_parser.parse_document_bytes  # returns a DoclingDocument
    assert sdk.build_structured is seam.build_structured


def test_model_deadline_is_read_at_call_time(monkeypatch):
    from rag_wright.models import seam

    monkeypatch.setattr(seam, "_MODEL_DEADLINE_S", 0.5)
    assert sdk.model_deadline_s() == 0.5


def test_store_config_from_the_environment(monkeypatch):
    for k in ("ARCADEDB_HOST", "ARCADEDB_PORT", "ARCADEDB_USER", "ARCADEDB_PROTOCOL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ARCADEDB_PASSWORD", "pw")
    assert api.StoreConfig.from_env() == api.StoreConfig("localhost", "2480", "root", "pw")
    monkeypatch.setenv("ARCADEDB_HOST", "db.internal")
    monkeypatch.setenv("ARCADEDB_PROTOCOL", "https")
    cfg = api.StoreConfig.from_env()
    assert (cfg.host, cfg.protocol) == ("db.internal", "https")
    monkeypatch.delenv("ARCADEDB_PASSWORD")
    with pytest.raises(KeyError):
        api.StoreConfig.from_env()
