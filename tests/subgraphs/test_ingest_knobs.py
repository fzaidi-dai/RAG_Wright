"""EP-API-4a (ADR-0117): the pure ingest-knob resolver. `None` -> the engine default (env fallback preserved so a
non-API caller is unaffected); an explicit config value overrides. `classify_concurrency` passes through (the
segment leaf owns its env fallback)."""
from __future__ import annotations

from rag_wright.packs.contracts.subgraphs.contract_ingestion_pipeline import _resolve_ingest_knobs


def test_defaults_fall_back_to_the_engine_defaults(monkeypatch):
    for var in ("CLAUSE_CONCURRENCY", "RAG_INGEST_AFFILIATIONS", "RAG_FUNCTION_CLASSIFIER"):
        monkeypatch.delenv(var, raising=False)
    cc, clause, aff, clf = _resolve_ingest_knobs(
        classify_concurrency=None, clause_concurrency=None, affiliations=None, function_classifier=None)
    assert cc is None and clause == 8 and aff is True and clf == "setfit"


def test_env_is_the_fallback_when_config_is_unset(monkeypatch):
    monkeypatch.setenv("CLAUSE_CONCURRENCY", "16")
    monkeypatch.setenv("RAG_INGEST_AFFILIATIONS", "0")
    monkeypatch.setenv("RAG_FUNCTION_CLASSIFIER", "llm")
    cc, clause, aff, clf = _resolve_ingest_knobs(
        classify_concurrency=None, clause_concurrency=None, affiliations=None, function_classifier=None)
    assert clause == 16 and aff is False and clf == "llm"


def test_config_overrides_env(monkeypatch):
    monkeypatch.setenv("CLAUSE_CONCURRENCY", "16")
    monkeypatch.setenv("RAG_INGEST_AFFILIATIONS", "0")
    monkeypatch.setenv("RAG_FUNCTION_CLASSIFIER", "llm")
    cc, clause, aff, clf = _resolve_ingest_knobs(
        classify_concurrency=4, clause_concurrency=5, affiliations=True, function_classifier="SetFit")
    assert cc == 4 and clause == 5 and aff is True and clf == "setfit"  # function_classifier lower-cased
