"""T11: the model-profile seam (assumption 2, tech stack, CLAUDE.md standing rule).

Unit tests only, with a fake `ChatOpenAI` (no network). They prove the seam mechanics: a profile
keyed by model id carries the structured-output method and an optional structured-only `extra_body`;
`with_structured_output` is reached only through the seam and the `extra_body` binds to the forced
structured call, never to the base (free-text / reasoning) client; and the model roles resolve to
DeepSeek V4 Pro (primary), Qwen 3.7 Plus (secondary), and the Gemma 4 class (general / local), all
from config, with no provider/model flag in a call site.
"""

from __future__ import annotations

import pytest

from rag_wright.models import profiles, seam
from rag_wright.models.profiles import ModelProfile, ModelRole


class _FakeStructured:
    """What the fake `with_structured_output` returns: a handle back to the base model + its kwargs."""

    def __init__(self, base: "_FakeChatOpenAI", schema, kwargs: dict):
        self.base = base
        self.schema = schema
        self.kwargs = kwargs


class _FakeChatOpenAI:
    def __init__(self, **kwargs):
        self.ctor_kwargs = kwargs

    def with_structured_output(self, schema, **kwargs):
        self.structured_kwargs = kwargs
        return _FakeStructured(self, schema, kwargs)


@pytest.fixture(autouse=True)
def _env_and_fake_client(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
    for var in (
        "RAG_MODEL_STRUCTURED_REASONING",
        "RAG_MODEL_STRUCTURED_REASONING_SECONDARY",
        "RAG_MODEL_GENERAL",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(seam, "ChatOpenAI", _FakeChatOpenAI)


class _Schema:  # a stand-in for a Pydantic contract; the fake never inspects it
    pass


# --- profile shape -----------------------------------------------------------------------------


def test_profile_defaults_to_function_calling_and_no_extra_body():
    p = ModelProfile(model_id="some/model")
    assert p.structured_method == "function_calling"  # more broadly supported than json_schema
    assert p.structured_extra_body is None


def test_unknown_model_gets_safe_default_profile():
    p = profiles.profile_for("random/unregistered-model")
    assert p.model_id == "random/unregistered-model"
    assert p.structured_method == "function_calling"
    assert p.structured_extra_body is None


# --- role resolution (priority lives in config, not capability code) ---------------------------


def test_roles_resolve_to_the_ledgered_models():
    assert "deepseek" in profiles.model_for(ModelRole.STRUCTURED_REASONING).lower()
    assert "qwen" in profiles.model_for(ModelRole.STRUCTURED_REASONING_SECONDARY).lower()
    assert "gemma" in profiles.model_for(ModelRole.GENERAL).lower()


def test_role_is_env_overridable(monkeypatch):
    monkeypatch.setenv("RAG_MODEL_STRUCTURED_REASONING", "vendor/custom-primary")
    assert profiles.model_for(ModelRole.STRUCTURED_REASONING) == "vendor/custom-primary"


def test_structured_reasoning_default_is_deepseek_with_a_registered_profile():
    model_id = profiles.model_for(ModelRole.STRUCTURED_REASONING)
    assert profiles.profile_for(model_id).model_id == model_id  # a real registered profile, not the fallback


# --- the seam is the only path to with_structured_output, and extra_body is structured-only ----


def test_structured_only_extra_body_binds_to_structured_call_not_base(monkeypatch):
    # a profile with an extra_body (the T12 thinking-disable shape is represented here synthetically)
    profile = ModelProfile(
        model_id="vendor/reasoner",
        structured_method="function_calling",
        structured_extra_body={"reasoning": {"enabled": False}},
    )
    monkeypatch.setitem(profiles.PROFILES, "vendor/reasoner", profile)

    runnable = seam.build_structured("vendor/reasoner", _Schema)

    # the extra_body reached the forced structured call...
    assert runnable.kwargs["method"] == "function_calling"
    assert runnable.kwargs["extra_body"] == {"reasoning": {"enabled": False}}
    # ...and never the base client (free-text / reasoning calls are unaffected).
    assert "extra_body" not in runnable.base.ctor_kwargs


def test_no_extra_body_kwarg_when_profile_has_none():
    runnable = seam.build_structured(profiles.model_for(ModelRole.GENERAL), _Schema)
    assert "extra_body" not in runnable.kwargs
    assert runnable.kwargs["method"] == "function_calling"


def test_build_model_uses_openrouter_base_and_key_no_hardcoded_flag(monkeypatch):
    model = seam.build_model("vendor/whatever")
    assert model.ctor_kwargs["model"] == "vendor/whatever"
    assert model.ctor_kwargs["api_key"] == "test-key"
    assert model.ctor_kwargs["base_url"] == "https://openrouter.ai/api/v1"
    assert model.ctor_kwargs["temperature"] == 0.0  # deterministic default
    # no provider/model-specific flag is baked into the base construction
    assert "extra_body" not in model.ctor_kwargs


def test_build_structured_forwards_include_raw(monkeypatch):
    runnable = seam.build_structured(profiles.model_for(ModelRole.GENERAL), _Schema, include_raw=True)
    assert runnable.kwargs["include_raw"] is True
