"""PS-19: a product deploys the self-hosted Qwen server through the engine and uses it like any other model.

`ModelServerSpec` defaults to the locked setup of the `qwen-vllm-modal` skill / ADR-0110 (Config B). Deploying runs
the shipped Modal script with that setup, waits for the server's `/health`, and registers a model profile for the
server with its own endpoint, so `server.model_id` works in `EngineConfig.models` or any `model=` argument. Modal
and HTTP are faked here; the live check deploys and stops a real server."""
from __future__ import annotations

import pytest

from rag_wright.api import (
    EngineConfig,
    ModelRole,
    ModelServerSpec,
    StoreConfig,
    adeploy_model_server,
    amodel_server,
    stop_model_server,
    use_workspace_models,
)
from rag_wright.api.workspace import WorkspaceHandle
from rag_wright.models import model_servers as ms
from rag_wright.models.profiles import PROFILES, model_for
from rag_wright.models.seam import resolve_connection

URL = "https://acme--acme-qwen-serve.modal.run"


@pytest.fixture
def modal_fake(monkeypatch):
    calls = {"cli": [], "health": 0}

    def cli(args, env):
        calls["cli"].append((list(args), dict(env)))

    def health(root, key):
        calls["health"] += 1
        return calls["health"] >= 2  # down on the first poll, ready on the second

    monkeypatch.setattr(ms, "_modal_cli", cli)
    monkeypatch.setattr(ms, "_web_url", lambda name: URL)
    monkeypatch.setattr(ms, "_healthy", health)
    monkeypatch.setenv("ACME_VLLM_KEY", "secret-key")
    saved = dict(PROFILES)
    yield calls
    PROFILES.clear()
    PROFILES.update(saved)


def test_the_defaults_are_the_skills_locked_config():
    env = ModelServerSpec(name="acme-qwen", max_num_seqs=32).deploy_env()
    assert env == {
        "APP_NAME": "acme-qwen", "MODEL": "Qwen/Qwen3.8-27B-FP8", "KV_CACHE_DTYPE": "fp8",
        "SERVED_NAME": "Qwen/Qwen3.8-27B", "TOOL_PARSER": "hermes", "REASONING_PARSER": "qwen3",
        "GPU": "A100-80GB:1", "TP": "1", "MAX_LEN": "16384", "GPU_UTIL": "0.95", "MAX_NUM_SEQS": "32",
        "MODAL_IMAGE_BUILDER_VERSION": "2025.06",
    }


def test_the_concurrency_target_must_be_stated():
    with pytest.raises(TypeError):
        ModelServerSpec(name="acme-qwen")  # ADR-0110: --max-num-seqs is the real concurrency target


async def test_deploy_runs_the_shipped_script_waits_and_registers_a_profile(modal_fake):
    spec = ModelServerSpec(name="acme-qwen", max_num_seqs=32, api_key_env="ACME_VLLM_KEY")
    server = await adeploy_model_server(spec, poll_s=0, progress=lambda _: None)

    (args, env), = modal_fake["cli"]
    assert args == ["deploy", str(ms.QWEN_MODAL_SCRIPT)] and ms.QWEN_MODAL_SCRIPT.is_file()
    assert env["VLLM_API_KEY"] == "secret-key" and env["MODEL"] == "Qwen/Qwen3.8-27B-FP8"
    assert modal_fake["health"] == 2  # waited until ready

    assert server.model_id == "qwen3.8-27b-modal@acme-qwen" and server.base_url == URL + "/v1"
    conn = resolve_connection(server.model_id)  # no VLLM_BASE_URL needed
    assert (conn.base_url, conn.api_key, conn.served_model_id) == (URL + "/v1", "secret-key", "Qwen/Qwen3.8-27B")
    template = PROFILES["qwen3.8-27b-modal"]  # the Qwen thinking settings carry over
    assert PROFILES[server.model_id].structured_extra_body == template.structured_extra_body
    assert PROFILES[server.model_id].text_extra_body == template.text_extra_body


async def test_the_server_is_used_like_any_model_through_a_workspace(modal_fake):
    server = await adeploy_model_server(ModelServerSpec(name="acme-qwen", max_num_seqs=32,
                                                        api_key_env="ACME_VLLM_KEY"), poll_s=0, progress=lambda _: None)
    ws = WorkspaceHandle(None, EngineConfig(store=StoreConfig(host="h", port="1", user="u", password="p"),
                                            models={ModelRole.GENERAL.value: server.model_id}), "acme")
    with use_workspace_models(ws):
        assert resolve_connection(model_for(ModelRole.GENERAL)).base_url == URL + "/v1"


async def test_deploy_refuses_without_the_api_key(modal_fake, monkeypatch):
    monkeypatch.delenv("ACME_VLLM_KEY")
    with pytest.raises(RuntimeError, match="ACME_VLLM_KEY"):
        await adeploy_model_server(ModelServerSpec(name="acme-qwen", max_num_seqs=32, api_key_env="ACME_VLLM_KEY"),
                                   poll_s=0, progress=lambda _: None)
    assert modal_fake["cli"] == []  # nothing deployed


async def test_waiting_gives_up_with_a_timeout(modal_fake, monkeypatch):
    monkeypatch.setattr(ms, "_healthy", lambda root, key: False)
    with pytest.raises(TimeoutError, match="acme-qwen"):
        await adeploy_model_server(ModelServerSpec(name="acme-qwen", max_num_seqs=32, api_key_env="ACME_VLLM_KEY"),
                                   timeout_s=0, poll_s=0, progress=lambda _: None)


async def test_reconnecting_registers_without_deploying(modal_fake):
    server = await amodel_server(ModelServerSpec(name="acme-qwen", max_num_seqs=32, api_key_env="ACME_VLLM_KEY"))
    assert modal_fake["cli"] == [] and resolve_connection(server.model_id).base_url == URL + "/v1"


def test_stop_stops_the_modal_app(modal_fake):
    stop_model_server("acme-qwen")
    assert modal_fake["cli"][0][0] == ["app", "stop", "acme-qwen", "--yes"]


def test_a_profile_with_an_explicit_endpoint_needs_no_environment(monkeypatch):
    from rag_wright.models.profiles import ModelProfile, register_model_profile

    monkeypatch.delenv("VLLM_BASE_URL", raising=False)
    saved = dict(PROFILES)
    try:
        register_model_profile(ModelProfile(model_id="m@x", backend="vllm", served_model_id="M",
                                            base_url="https://x/v1"))
        assert resolve_connection("m@x").base_url == "https://x/v1"
    finally:
        PROFILES.clear()
        PROFILES.update(saved)
