"""ADR-0119: the generic `jev_decision` model capability. Hermetic tests stub the HTTP call and prove the request
shape + parsing, that it's invocable by name as an ASYNC model cap via `ainvoke_model`, and that the sync
`invoke_model` refuses it. A `-m model` live test hits the real OpenRouter Decisions API."""
from __future__ import annotations

import os

import pytest

import rag_wright.capabilities.jev_decision as jd
from rag_wright.api import ainvoke_model, invoke_model
from rag_wright.api import EngineConfig, StoreConfig, WorkspaceHandle


def _handle():
    return WorkspaceHandle(store=object(), config=EngineConfig(store=StoreConfig(host="h", port="1", user="u", password="p")), corpus="c")


class _Resp:
    def __init__(self, data):
        self._d = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._d


class _Client:
    captured = {}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None, timeout=None):
        _Client.captured = {"url": url, "headers": headers, "body": json}
        return _Resp({"answers": {"operative": {"type": "noul", "noul": 0.93}}, "usage": {"cost": 1e-5}})


@pytest.fixture
def _stub_http(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")


async def test_jev_decision_builds_request_and_parses(_stub_http):
    out = await jd.jev_decision(None, {"state": "Advertisers must disclose connections.",
                                       "questions": {"operative": {"type": "noul", "instructions": "binding rule?",
                                                                    "criteria": {"true": "rule", "false": "not"}}}})
    assert out["answers"]["operative"]["noul"] == 0.93
    b = _Client.captured["body"]
    assert b["model"] == "typesafe/jev-1.13" and b["state"].startswith("Advertisers")
    assert "operative" in b["questions"] and b["questions"]["operative"]["type"] == "noul"
    assert _Client.captured["headers"]["Authorization"] == "Bearer test-key"


async def test_jev_decision_invocable_by_name_via_ainvoke_model(_stub_http):
    out = await ainvoke_model("jev_decision",
                              {"state": "x", "questions": {"q": {"type": "noul", "instructions": "?",
                                                                 "criteria": {"true": "a", "false": "b"}}}},
                              resources=_handle())
    assert out["answers"]["operative"]["noul"] == 0.93  # routed through the impl_ref to the stubbed client


def test_sync_invoke_model_refuses_the_async_jev_cap():
    # jev_decision is an async impl; the sync invoker must redirect to ainvoke_model (EP-API-7 guard).
    with pytest.raises(TypeError, match="ainvoke_model"):
        invoke_model("jev_decision", {"state": "x", "questions": {}}, resources=_handle())


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    import asyncio
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        asyncio.run(jd.jev_decision(None, {"state": "x", "questions": {}}))


# --- live: real Jev via OpenRouter (opt-in) ---

@pytest.mark.model
@pytest.mark.skipif(not os.getenv("OPENROUTER_API_KEY"), reason="OPENROUTER_API_KEY not set")
def test_jev_decision_live():
    import asyncio
    out = asyncio.run(jd.jev_decision(None, {
        "state": "The advertiser must clearly and conspicuously disclose any material connection.",
        "questions": {"operative": {"type": "noul", "instructions": "Is this a binding operative rule?",
                                    "criteria": {"true": "imposes a binding obligation/prohibition/permission",
                                                 "false": "an example, definition, cross-reference, or description"}}}}))
    v = out["answers"]["operative"]["noul"]
    assert 0.0 <= v <= 1.0 and v > 0.5  # a clear 'must' obligation -> rule


# --- ADR-0119: the decision-model profile ---


def test_decision_profile_resolves_and_defaults():
    from rag_wright.models.profiles import decision_profile
    p = decision_profile()  # default
    assert p.model_id == "jev-1.13" and p.served == "typesafe/jev-1.13"
    assert p.endpoint.endswith("/api/alpha/decisions") and p.api_key_env == "OPENROUTER_API_KEY"
    assert 0.0 < p.op_threshold <= 1.0 and 0.0 < p.multilabel_threshold <= 1.0
    # a raw/unregistered id still works (used as its own served id) -> swapping is config, not code
    raw = decision_profile("typesafe/jev-latest")
    assert raw.served == "typesafe/jev-latest"


def test_jev_decision_uses_profile_served_id_and_key_env(_stub_http):
    import asyncio
    asyncio.run(jd.jev_decision(None, {"state": "x", "questions": {"q": {"type": "noul", "instructions": "?",
                                                                         "criteria": {"true": "a", "false": "b"}}}}))
    assert _Client.captured["body"]["model"] == "typesafe/jev-1.13"  # resolved from the profile, not hardcoded


async def test_a_jev_call_is_metered_like_any_model_call(_stub_http):
    """ING-4c: the decisions endpoint returns its own `usage` (cost); it lands in `measure_usage` -- a paid call is
    never invisible to the meter."""
    from rag_wright.api import measure_usage

    with measure_usage() as usage:
        await jd.jev_decision(None, {"state": "x", "questions": {"q": {"type": "noul", "instructions": "?",
                                                                      "criteria": {"true": "a", "false": "b"}}}})
    assert usage.calls == 1 and usage.cost_usd == pytest.approx(1e-5) and usage.calls_without_cost == 0
    assert list(usage.by_model) == ["jev-1.13"]
