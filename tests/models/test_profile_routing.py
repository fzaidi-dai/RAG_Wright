"""ADR-0100: a model STRING resolves to a PROFILE that carries how to reach it (backend + base_url + key +
served id). A string can PIN a backend (mix OpenRouter + self-hosted vLLM/Modal per stage); an un-pinned string
falls back to the global `RAG_SERVING` (back-compat). Hermetic -- only env + the pure resolver."""

from __future__ import annotations

import pytest

from rag_wright.models.seam import resolve_connection


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("VLLM_BASE_URL", "https://myqwen.modal.run/v1")
    monkeypatch.setenv("VLLM_API_KEY", "rw-vllm-key")
    monkeypatch.delenv("RAG_SERVING", raising=False)


def test_pinned_openrouter_string_routes_to_openrouter():
    c = resolve_connection("qwen3.8-27b-or")
    assert c.backend == "openrouter" and c.provider == "openrouter"
    assert c.served_model_id == "qwen/qwen3.8-27b"  # the slug the backend expects, != our string
    assert c.base_url == "https://openrouter.ai/api/v1"


def test_pinned_vllm_string_routes_to_the_self_hosted_server():
    c = resolve_connection("qwen3.8-27b-modal")
    assert c.backend == "vllm" and c.provider == "hosted_vllm"
    assert c.served_model_id == "Qwen/Qwen3.8-27B"  # the vLLM --served-model-name
    assert c.base_url == "https://myqwen.modal.run/v1" and c.api_key == "rw-vllm-key"


def test_unpinned_string_falls_back_to_rag_serving(monkeypatch):
    monkeypatch.setenv("RAG_SERVING", "openrouter")
    assert resolve_connection("qwen/qwen3.8-27b").backend == "openrouter"  # un-pinned -> RAG_SERVING
    monkeypatch.setenv("RAG_SERVING", "vllm")
    c = resolve_connection("ibm-granite/granite-4.2-8b")  # BACK-COMPAT: same as before this change
    assert c.backend == "vllm" and c.served_model_id == "ibm-granite/granite-4.2-8b"


def test_base_url_env_override_lets_a_second_server_be_just_another_string(monkeypatch):
    from rag_wright.models import profiles

    monkeypatch.setitem(
        profiles.PROFILES, "qwen-server2",
        profiles.ModelProfile(model_id="qwen-server2", backend="vllm", served_model_id="Qwen/Qwen3.8-27B",
                              base_url_env="VLLM_BASE_URL_2"))
    monkeypatch.setenv("VLLM_BASE_URL_2", "https://second-qwen.modal.run/v1")
    assert resolve_connection("qwen-server2").base_url == "https://second-qwen.modal.run/v1"


def test_build_model_uses_the_resolved_connection(monkeypatch):
    # build_model constructs the client with the profile's served id + base_url + key (not a global switch)
    import rag_wright.models.seam as seam

    captured = {}

    class _FakeClient:
        def __init__(self, **kw):
            captured.update(kw)

    monkeypatch.setattr(seam, "ChatOpenAI", _FakeClient)
    seam.build_model("qwen3.8-27b-modal")
    assert captured["model"] == "Qwen/Qwen3.8-27B"  # served id, not our string
    assert captured["base_url"] == "https://myqwen.modal.run/v1" and captured["api_key"] == "rw-vllm-key"


def test_default_extraction_model_routes_via_profile():
    from rag_wright.packs.contracts.capabilities.dg_extraction import default_extraction_model

    m = default_extraction_model("clause-extract", "qwen3.8-27b-modal")
    assert m.provider == "hosted_vllm" and m.model == "Qwen/Qwen3.8-27B"
    assert m.base_url == "https://myqwen.modal.run/v1"
    # and the OpenRouter-pinned variant of the SAME model routes to OpenRouter (mix per stage)
    m2 = default_extraction_model("claim-extract", "qwen3.8-27b-or")
    assert m2.provider == "openrouter" and m2.model == "qwen/qwen3.8-27b"
