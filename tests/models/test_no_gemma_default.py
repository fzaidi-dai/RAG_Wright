"""PS-17: Gemma is never a default. It is reachable only when a config, the environment or a product names it (its
profiles stay registered so that works correctly); no role default, public constant or default-on path selects it."""
from __future__ import annotations

import pytest

from rag_wright import pack_sdk
from rag_wright.models import profiles
from rag_wright.models.profiles import ModelRole, model_for, profile_for


@pytest.fixture(autouse=True)
def _no_env_overrides(monkeypatch):
    for role in ModelRole:
        monkeypatch.delenv(f"RAG_MODEL_{role.name}", raising=False)
    for name in ("RAG_MODEL_ALL", "RAG_INGEST_LIST_MODEL"):
        monkeypatch.delenv(name, raising=False)


def test_no_role_defaults_to_gemma():
    assert not [r for r in ModelRole if "gemma" in model_for(r).lower()]


def test_no_public_constant_names_gemma():
    for module in (profiles, pack_sdk):
        hits = [n for n in dir(module) if n.isupper() and isinstance(getattr(module, n), str)
                and "gemma" in getattr(module, n).lower()]
        assert hits == [], module.__name__
    assert not hasattr(pack_sdk, "DEFAULT_GENERAL")


def test_gemma_stays_selectable_through_config():
    assert profile_for("google/gemma-4-31b-it").model_id == "google/gemma-4-31b-it"
    assert "google/gemma-4-31b-it" in profiles.PROFILES


def test_the_legacy_extractor_runs_no_second_model_unless_one_is_named():
    from rag_wright.packs.contracts.spans.tag_clause_extractor import list_model_for

    assert list_model_for(None) is None
    assert list_model_for("google/gemma-4-31b-it") == "google/gemma-4-31b-it"
    assert list_model_for("off") is None
