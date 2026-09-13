"""MS1-1 (ADR-0039): the config-driven serving seam. `RAG_SERVING` selects OpenRouter (default/dev+fallback)
or vLLM-Granite (product) WITHOUT hardcoding a provider. Hermetic -- ChatOpenAI construction makes no network
call, so build_model is exercised offline."""

from __future__ import annotations

import pytest

from rag_wright.models.seam import _provider_pin, _serving_config, build_model


@pytest.fixture(autouse=True)
def _clean_serving_env(monkeypatch):
    # start from a known state: OpenRouter creds present, no vLLM/serving override
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
    monkeypatch.delenv("RAG_SERVING", raising=False)
    monkeypatch.delenv("VLLM_BASE_URL", raising=False)
    monkeypatch.delenv("VLLM_API_KEY", raising=False)


def test_default_serving_is_openrouter():
    cfg = _serving_config()
    assert cfg == {"api_key": "or-key", "base_url": "https://openrouter.ai/api/v1"}


def test_explicit_openrouter_matches_default(monkeypatch):
    monkeypatch.setenv("RAG_SERVING", "openrouter")
    assert _serving_config()["base_url"] == "https://openrouter.ai/api/v1"


def test_provider_pin_empty_when_unset(monkeypatch):
    monkeypatch.delenv("OPENROUTER_PROVIDER", raising=False)
    assert _provider_pin() == {}


def test_provider_pin_single_is_a_hard_pin(monkeypatch):
    monkeypatch.setenv("OPENROUTER_PROVIDER", "Cerebras")
    monkeypatch.delenv("OPENROUTER_ALLOW_FALLBACKS", raising=False)
    assert _provider_pin() == {"provider": {"only": ["Cerebras"], "allow_fallbacks": False}}


def test_provider_pin_ordered_list_with_fallbacks(monkeypatch):
    monkeypatch.setenv("OPENROUTER_PROVIDER", "deepinfra/turbo, Cerebras, friendli")
    monkeypatch.setenv("OPENROUTER_ALLOW_FALLBACKS", "true")
    assert _provider_pin() == {
        "provider": {"order": ["deepinfra/turbo", "Cerebras", "friendli"], "allow_fallbacks": True}
    }


def test_vllm_serving_reads_base_url_and_key(monkeypatch):
    monkeypatch.setenv("RAG_SERVING", "vllm")
    monkeypatch.setenv("VLLM_BASE_URL", "https://app.modal.run/v1/")  # trailing slash stripped
    monkeypatch.setenv("VLLM_API_KEY", "vk")
    assert _serving_config() == {"api_key": "vk", "base_url": "https://app.modal.run/v1"}


def test_vllm_api_key_defaults_when_unset(monkeypatch):
    monkeypatch.setenv("RAG_SERVING", "vllm")
    monkeypatch.setenv("VLLM_BASE_URL", "https://app.modal.run/v1")
    assert _serving_config()["api_key"] == "rw-vllm-dev-key"


def test_vllm_requires_base_url(monkeypatch):
    monkeypatch.setenv("RAG_SERVING", "vllm")  # no VLLM_BASE_URL
    with pytest.raises(KeyError):
        _serving_config()


def test_unknown_serving_raises(monkeypatch):
    monkeypatch.setenv("RAG_SERVING", "bedrock")
    with pytest.raises(ValueError, match="RAG_SERVING"):
        _serving_config()


def test_case_insensitive_serving(monkeypatch):
    monkeypatch.setenv("RAG_SERVING", "VLLM")
    monkeypatch.setenv("VLLM_BASE_URL", "https://app.modal.run/v1")
    assert _serving_config()["base_url"] == "https://app.modal.run/v1"


def test_build_model_uses_openrouter_base_url_by_default():
    # ChatOpenAI construction is offline; verify the client points at OpenRouter
    assert build_model("ibm-granite/granite-4.2-8b").openai_api_base == "https://openrouter.ai/api/v1"


def test_build_model_routes_to_vllm_when_selected(monkeypatch):
    monkeypatch.setenv("RAG_SERVING", "vllm")
    monkeypatch.setenv("VLLM_BASE_URL", "https://app.modal.run/v1")
    assert build_model("ibm-granite/granite-4.2-8b").openai_api_base == "https://app.modal.run/v1"


def test_build_model_defaults_to_temperature_zero_and_honors_override():
    assert build_model("ibm-granite/granite-4.2-8b").temperature == 0.0
    assert build_model("ibm-granite/granite-4.2-8b", temperature=0.7).temperature == 0.7  # best-of-N sampling


def test_build_structured_forwards_temperature(monkeypatch):
    """C (best-of-N) raises the structured call's temperature for diverse samples -- verify it reaches
    build_model (default stays 0, so ordinary structured calls are unchanged)."""
    from rag_wright.models import seam

    seen = {}

    class _StubRunnable:
        def with_structured_output(self, schema, **kwargs):
            return self
        def with_retry(self, **kwargs):
            return self
        def __or__(self, other):  # issue 0042: the seam pipes `| _raise_on_parse_error`; passthrough for the stub
            return self

    def _fake_build_model(model_id, *, temperature=0.0, **overrides):
        seen["temperature"] = temperature
        return _StubRunnable()

    monkeypatch.setattr(seam, "build_model", _fake_build_model)
    seam.build_structured("ibm-granite/granite-4.2-8b", object)
    assert seen["temperature"] == 0.0  # default unchanged
    seam.build_structured("ibm-granite/granite-4.2-8b", object, temperature=0.7)
    assert seen["temperature"] == 0.7
