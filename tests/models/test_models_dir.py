"""PS-5 (G20): one models root for every trained classifier -- `RAG_MODELS_DIR`, else the engine checkout's
`data/models` when it exists, else `./data/models` (a product's own copy) -- honoured by every reference-pack loader."""
from __future__ import annotations

from pathlib import Path

import pytest

from rag_wright.models import weights
from rag_wright.models.weights import models_dir


def test_the_env_var_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("RAG_MODELS_DIR", str(tmp_path))
    assert models_dir() == tmp_path


def test_the_engine_checkout_is_the_default_when_it_has_models(monkeypatch, tmp_path):
    monkeypatch.delenv("RAG_MODELS_DIR", raising=False)
    (tmp_path / "data" / "models").mkdir(parents=True)
    monkeypatch.setattr(weights, "_CHECKOUT", tmp_path)
    assert models_dir() == tmp_path / "data" / "models"


def test_an_installed_engine_falls_back_to_the_working_directory(monkeypatch, tmp_path):
    monkeypatch.delenv("RAG_MODELS_DIR", raising=False)
    monkeypatch.setattr(weights, "_CHECKOUT", tmp_path / "site-packages")  # no data/models next to the package
    monkeypatch.chdir(tmp_path)
    assert models_dir() == Path("data") / "models"


def test_the_property_fleet_loads_from_the_models_root(monkeypatch, tmp_path):
    from rag_wright.packs.contracts.spans.dim_classifier import load_dim_registry

    monkeypatch.setenv("RAG_MODELS_DIR", str(tmp_path))
    with pytest.raises(FileNotFoundError, match=str(tmp_path)):
        load_dim_registry()


def test_the_clause_classifier_loads_from_the_models_root(monkeypatch, tmp_path):
    from rag_wright.packs.contracts.spans.clause_function_classifier import production_setfit_clause_classifier

    monkeypatch.delenv("RAG_SETFIT_CLAUSE_DIR", raising=False)
    monkeypatch.setenv("RAG_MODELS_DIR", str(tmp_path))
    with pytest.raises(FileNotFoundError, match=str(tmp_path / "setfit_clause")):
        production_setfit_clause_classifier()


def test_the_clause_specific_override_still_wins(monkeypatch, tmp_path):
    from rag_wright.packs.contracts.spans.clause_function_classifier import production_setfit_clause_classifier

    monkeypatch.setenv("RAG_MODELS_DIR", str(tmp_path / "root"))
    monkeypatch.setenv("RAG_SETFIT_CLAUSE_DIR", str(tmp_path / "clause"))
    with pytest.raises(FileNotFoundError, match=str(tmp_path / "clause")):
        production_setfit_clause_classifier()


def test_the_query_classifier_loads_from_the_models_root(monkeypatch, tmp_path):
    from rag_wright.packs.contracts.spans import legalbert_classifier

    seen = {}
    monkeypatch.delenv("STACK_URL", raising=False)
    monkeypatch.setenv("RAG_MODELS_DIR", str(tmp_path))
    monkeypatch.setattr(legalbert_classifier.LegalBertFunctionClassifier, "load",
                        classmethod(lambda cls, path, **kw: seen.setdefault("path", path)))
    legalbert_classifier.query_classifier()
    assert seen["path"] == tmp_path / "legalbert_function"
