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
        self.retry_kwargs: dict | None = None

    def with_retry(self, **kwargs):
        """Model the seam's `.with_retry` wrap transparently: record the config, stay inspectable."""
        self.retry_kwargs = kwargs
        return self


class _FakeChatOpenAI:
    # build_structured now returns a bounded-retry wrapper around the structured handle (ADR-0056), not the
    # handle itself, so capture the last-built handle here for inspection.
    last_structured: "_FakeStructured | None" = None

    def __init__(self, **kwargs):
        self.ctor_kwargs = kwargs

    def with_structured_output(self, schema, **kwargs):
        self.structured_kwargs = kwargs
        _FakeChatOpenAI.last_structured = _FakeStructured(self, schema, kwargs)
        return _FakeChatOpenAI.last_structured


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


def test_every_role_defaults_to_single_granite():
    # MS1-2 (ADR-0039): the product substrate is a single self-hosted Granite for EVERY role (OKF included --
    # it is not in the ingestion/query pipeline). Gemma/DeepSeek dropped from the default (still registered).
    for role in ModelRole:
        assert "granite" in profiles.model_for(role).lower()


def test_role_is_env_overridable(monkeypatch):
    monkeypatch.setenv("RAG_MODEL_STRUCTURED_REASONING", "vendor/custom-primary")
    assert profiles.model_for(ModelRole.STRUCTURED_REASONING) == "vendor/custom-primary"


def test_all_roles_override_points_every_role_at_one_model(monkeypatch):
    monkeypatch.setenv("RAG_MODEL_ALL", "vendor/experiment")
    for role in ModelRole:
        assert profiles.model_for(role) == "vendor/experiment"


def test_role_specific_override_wins_over_all_roles_override(monkeypatch):
    monkeypatch.setenv("RAG_MODEL_ALL", "vendor/experiment")
    monkeypatch.setenv("RAG_MODEL_GENERAL", "vendor/just-general")
    assert profiles.model_for(ModelRole.GENERAL) == "vendor/just-general"  # per-role wins
    assert profiles.model_for(ModelRole.SUMMARIZATION) == "vendor/experiment"  # others take the all-roles value


def test_dropped_foundation_models_stay_registered_for_dev_override(monkeypatch):
    # the product default is Granite, but a dev run can still select a foundation model and get its REAL
    # profile (not the safe fallback) -- the profiles are dropped from the default, not deregistered.
    for model_id in (
        profiles.DEFAULT_STRUCTURED_REASONING,  # deepseek
        profiles.DEFAULT_GENERAL,  # gemma
        profiles.DEFAULT_STRUCTURED_REASONING_SECONDARY,  # qwen
    ):
        assert profiles.profile_for(model_id).model_id == model_id
    # and the product default itself is a registered profile (json_schema), not the fallback
    granite = profiles.model_for(ModelRole.STRUCTURED_REASONING)
    assert profiles.profile_for(granite).model_id == granite
    assert profiles.profile_for(granite).structured_method == "json_schema"


# --- the seam is the only path to with_structured_output, and extra_body is structured-only ----


def test_structured_only_extra_body_binds_to_structured_call_not_base(monkeypatch):
    # a profile with an extra_body (the T12 thinking-disable shape is represented here synthetically)
    profile = ModelProfile(
        model_id="vendor/reasoner",
        structured_method="function_calling",
        structured_extra_body={"reasoning": {"enabled": False}},
    )
    monkeypatch.setitem(profiles.PROFILES, "vendor/reasoner", profile)

    seam.build_structured("vendor/reasoner", _Schema)
    structured = _FakeChatOpenAI.last_structured

    # the extra_body reached the forced structured call...
    assert structured.kwargs["method"] == "function_calling"
    assert structured.kwargs["extra_body"] == {"reasoning": {"enabled": False}}
    # ...and never the base client (free-text / reasoning calls are unaffected).
    assert "extra_body" not in structured.base.ctor_kwargs


def test_no_extra_body_kwarg_when_profile_has_none(monkeypatch):
    """A profile with no structured_extra_body adds no extra_body kwarg to the forced structured call.

    Uses an isolated plain profile rather than a real role: real roles may carry a structured_extra_body per
    their own ADR (e.g. the GENERAL/Gemma-4 profile disables reasoning on the forced structured call, CU-D2/
    ADR-0032), so this invariant must not assume any role has none."""
    monkeypatch.setitem(
        profiles.PROFILES, "vendor/plain",
        ModelProfile(model_id="vendor/plain", structured_method="function_calling"),
    )
    seam.build_structured("vendor/plain", _Schema)
    structured = _FakeChatOpenAI.last_structured
    assert "extra_body" not in structured.kwargs
    assert structured.kwargs["method"] == "function_calling"


# --- base extra_body (provider routing, ADR-0027): binds to the BASE client, every call --------


def test_base_extra_body_binds_to_the_base_client(monkeypatch):
    profile = ModelProfile(model_id="vendor/routed", extra_body={"provider": {"sort": "throughput"}})
    monkeypatch.setitem(profiles.PROFILES, "vendor/routed", profile)
    client = seam.build_model("vendor/routed")
    assert client.ctor_kwargs["extra_body"] == {"provider": {"sort": "throughput"}}  # on the base client


def test_deepseek_v4_pro_profile_routes_by_throughput():
    # the extraction default (DeepSeek V4 Pro) prefers the fastest provider, not the cheapest (ADR-0027)
    p = profiles.profile_for(profiles.DEFAULT_STRUCTURED_REASONING)
    assert p.extra_body == {"provider": {"sort": "throughput"}}
    assert seam.build_model(profiles.DEFAULT_STRUCTURED_REASONING).ctor_kwargs["extra_body"] == p.extra_body


def test_build_model_uses_openrouter_base_and_key_no_hardcoded_flag(monkeypatch):
    model = seam.build_model("vendor/whatever")
    assert model.ctor_kwargs["model"] == "vendor/whatever"
    assert model.ctor_kwargs["api_key"] == "test-key"
    assert model.ctor_kwargs["base_url"] == "https://openrouter.ai/api/v1"
    assert model.ctor_kwargs["temperature"] == 0.0  # deterministic default
    # no provider/model-specific flag is baked into the base construction
    assert "extra_body" not in model.ctor_kwargs


def test_build_structured_forwards_include_raw(monkeypatch):
    seam.build_structured(profiles.model_for(ModelRole.GENERAL), _Schema, include_raw=True)
    assert _FakeChatOpenAI.last_structured.kwargs["include_raw"] is True


def test_build_model_sets_connection_resilience_retry_and_timeout():
    model = seam.build_model("vendor/whatever")
    assert model.ctor_kwargs["max_retries"] == seam._MAX_RETRIES  # framework connection resilience, from the seam
    assert model.ctor_kwargs["timeout"] == seam._TIMEOUT_S
    # a caller may still override
    assert seam.build_model("vendor/whatever", max_retries=0).ctor_kwargs["max_retries"] == 0


def test_build_structured_disables_the_sdk_retry_loop_with_a_tighter_timeout():
    # engine issue 0003 / ADR-0056: the SDK's own retry loop is OFF for structured calls (no stacking), with a
    # tighter per-request timeout.
    seam.build_structured(profiles.model_for(ModelRole.GENERAL), _Schema)
    base = _FakeChatOpenAI.last_structured.base
    assert base.ctor_kwargs["max_retries"] == 0
    assert base.ctor_kwargs["timeout"] == seam._STRUCTURED_TIMEOUT_S


def test_build_structured_wires_both_the_sync_and_async_paths():
    # ADR-0057 dual-path during the migration: `.invoke` keeps the sync bounded retry (ADR-0056) and `.ainvoke`
    # routes through the async bounded-retry + true wall-clock deadline (an afunc is set on the returned lambda).
    runnable = seam.build_structured(profiles.model_for(ModelRole.GENERAL), _Schema)
    assert getattr(runnable, "afunc", None) is not None  # async deadline path wired
    assert callable(runnable.invoke)  # sync bounded-retry path preserved
