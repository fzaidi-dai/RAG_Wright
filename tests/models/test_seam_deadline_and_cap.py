"""PS-22: the async deadline follows the configured per-try timeout, and a profile caps a thinking model's
structured output so a reasoning loop ends at the cap instead of running into the deadline.

Hermetic: a fake `ChatOpenAI` (no network).
"""
from __future__ import annotations

import httpx
import openai
import pytest
from langchain_core.runnables import RunnableLambda

from rag_wright.models import profiles, seam
from rag_wright.models.seam import ModelCallTimeout, _ainvoke_bounded, build_structured
from tests.async_helpers import FakeAsyncRunnable

_THINKING_QWEN = ("qwen/qwen3.8-27b", "qwen3.8-27b-or", "qwen3.8-27b-modal", "qwen3.8-27b-modal-or")


def _context_error() -> openai.BadRequestError:
    """vLLM's 400 when the prompt plus `max_tokens` exceeds the context (`param: input_tokens`)."""
    request = httpx.Request("POST", "http://vllm.local/v1/chat/completions")
    return openai.BadRequestError(
        "This model's maximum context length is 16384 tokens. However, you requested 8192 output tokens and "
        "your prompt contains 9000 input tokens", response=httpx.Response(400, request=request),
        body={"type": "BadRequestError", "param": "input_tokens"})


class _FakeChatOpenAI:
    """Builds a structured runnable that fails like vLLM when this client was given `max_tokens`."""

    built: list[dict] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        _FakeChatOpenAI.built.append(kwargs)

    def with_structured_output(self, schema, **_kwargs):
        capped = self.kwargs.get("max_tokens") is not None

        def run(_x):
            if capped and _FakeChatOpenAI.fail_when_capped:
                raise _FakeChatOpenAI.fail_when_capped
            return {"raw": None, "parsed": f"ok (max_tokens={self.kwargs.get('max_tokens')})", "parsing_error": None}

        return RunnableLambda(run)

    fail_when_capped: Exception | None = None


@pytest.fixture(autouse=True)
def _fake_client(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("VLLM_BASE_URL", "http://vllm.local/v1")
    monkeypatch.setenv("VLLM_API_KEY", "test-key")
    monkeypatch.setattr(seam, "ChatOpenAI", _FakeChatOpenAI)
    _FakeChatOpenAI.built = []
    _FakeChatOpenAI.fail_when_capped = None


# --- the deadline ---------------------------------------------------------------------------------------------


def test_the_default_deadline_covers_every_try_at_the_configured_timeout(monkeypatch):
    monkeypatch.setattr(seam, "_MODEL_DEADLINE_S", None)  # not set
    monkeypatch.setattr(seam, "_STRUCTURED_TIMEOUT_S", 600.0)  # raised for reasoning-on bulk work
    assert seam.model_deadline_s() >= 600.0 * seam._STRUCTURED_RETRY_ATTEMPTS


def test_the_default_deadline_is_unchanged_at_the_default_timeout(monkeypatch):
    monkeypatch.setattr(seam, "_MODEL_DEADLINE_S", None)
    monkeypatch.setattr(seam, "_STRUCTURED_TIMEOUT_S", 60.0)
    assert 180.0 <= seam.model_deadline_s() <= 200.0


def test_an_explicit_deadline_wins(monkeypatch):
    monkeypatch.setattr(seam, "_MODEL_DEADLINE_S", 900.0)
    monkeypatch.setattr(seam, "_STRUCTURED_TIMEOUT_S", 600.0)
    assert seam.model_deadline_s() == 900.0


async def test_the_async_path_runs_under_model_deadline_s_and_names_the_setting(monkeypatch):
    monkeypatch.setattr(seam, "_MODEL_DEADLINE_S", 0.05)
    with pytest.raises(ModelCallTimeout, match="RAG_MODEL_DEADLINE_S"):
        await _ainvoke_bounded(FakeAsyncRunnable(stall_s=5.0), "x", "test-model")


# --- the profile token cap ------------------------------------------------------------------------------------


@pytest.mark.parametrize("model_id", _THINKING_QWEN)
def test_thinking_qwen_profiles_cap_structured_output(model_id):
    assert profiles.PROFILES[model_id].structured_max_tokens == 8192


def test_a_profile_without_a_cap_sends_no_max_tokens():
    assert profiles.profile_for("some/unregistered-model").structured_max_tokens is None
    build_structured("some/unregistered-model", dict).invoke("x")
    assert all("max_tokens" not in kw for kw in _FakeChatOpenAI.built)


def test_the_profile_cap_is_sent_when_the_caller_sets_none():
    assert build_structured("qwen3.8-27b-modal", dict).invoke("x") == "ok (max_tokens=8192)"


def test_a_callers_max_tokens_wins_over_the_profile_cap():
    assert build_structured("qwen3.8-27b-modal", dict, max_tokens=100).invoke("x") == "ok (max_tokens=100)"


def test_a_prompt_too_long_for_the_cap_is_retried_without_it():
    _FakeChatOpenAI.fail_when_capped = _context_error()
    assert build_structured("qwen3.8-27b-modal", dict).invoke("x") == "ok (max_tokens=None)"


async def test_a_prompt_too_long_for_the_cap_is_retried_without_it_async():
    _FakeChatOpenAI.fail_when_capped = _context_error()
    assert await build_structured("qwen3.8-27b-modal", dict).ainvoke("x") == "ok (max_tokens=None)"


def test_a_callers_own_max_tokens_is_not_dropped_on_a_context_error():
    _FakeChatOpenAI.fail_when_capped = _context_error()
    with pytest.raises(openai.BadRequestError):
        build_structured("qwen3.8-27b-modal", dict, max_tokens=100).invoke("x")


def test_any_other_bad_request_still_fails():
    request = httpx.Request("POST", "http://vllm.local/v1/chat/completions")
    _FakeChatOpenAI.fail_when_capped = openai.BadRequestError(
        "bad tool schema", response=httpx.Response(400, request=request), body={"param": "tools"})
    with pytest.raises(openai.BadRequestError):
        build_structured("qwen3.8-27b-modal", dict).invoke("x")
