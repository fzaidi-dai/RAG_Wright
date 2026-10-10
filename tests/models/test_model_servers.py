"""PS-19: a product deploys the self-hosted Qwen server through the engine and uses it like any other model.

`ModelServerSpec` defaults to the locked setup of the `qwen-vllm-modal` skill / ADR-0110 (Config B). Deploying runs
the shipped Modal script with that setup, waits for the server's `/health`, and registers a model profile for the
server with its own endpoint, so `server.model_id` works in `EngineConfig.models` or any `model=` argument. Modal
and HTTP are faked here; the live check deploys and stops a real server."""
from __future__ import annotations

import types

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
    async def web_url(name):
        return URL

    monkeypatch.setattr(ms, "_web_url", web_url)
    monkeypatch.setattr(ms, "_healthy", health)

    async def runners(name):
        return calls.get("runners", 1)

    monkeypatch.setattr(ms, "_runners", runners)
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


async def test_the_url_lookup_uses_modals_async_interface(monkeypatch):
    """Modal warns (AsyncUsageWarning) when its blocking interface runs inside an event loop; the deploy and reconnect
    paths are async, so the lookup must use the `.aio` form."""
    import sys
    import types

    class WebUrl:
        def __call__(self):
            raise AssertionError("the blocking get_web_url() was called from async code")

        async def aio(self):
            return URL + "/"

    fake = types.SimpleNamespace(Function=types.SimpleNamespace(
        from_name=lambda name, fn: types.SimpleNamespace(get_web_url=WebUrl())))
    monkeypatch.setitem(sys.modules, "modal", fake)
    assert await ms._web_url("acme-qwen") == URL


def test_stop_stops_the_modal_app(modal_fake):
    stop_model_server("acme-qwen")
    assert modal_fake["cli"][0][0] == ["app", "stop", "acme-qwen", "--yes"]


def test_a_profile_with_an_explicit_endpoint_needs_no_environment(monkeypatch):
    from rag_wright.models.profiles import ModelProfile, register_model_profile

    monkeypatch.delenv("VLLM_BASE_URL", raising=False)
    monkeypatch.setenv("VLLM_API_KEY", "vk")
    saved = dict(PROFILES)
    try:
        register_model_profile(ModelProfile(model_id="m@x", backend="vllm", served_model_id="M",
                                            base_url="https://x/v1"))
        assert resolve_connection("m@x").base_url == "https://x/v1"
    finally:
        PROFILES.clear()
        PROFILES.update(saved)


# PS-21: RuleWright's model-server review.

REPO = ms.QWEN_MODAL_SCRIPT.parents[6]


def test_no_server_script_or_engine_path_defaults_the_api_key():
    """(1) A default key would be the only gate on a public, billing A100 (Modal's web endpoint is unauthenticated;
    vLLM's --api-key is the gate), and it would be printed in the repo. Nothing may fall back to one."""
    places = [*sorted((REPO / "src").rglob("*.py")), *sorted((REPO / "src").rglob("*.md")),
              *sorted((REPO / "scripts").glob("*.py")), *sorted((REPO / "docs").glob("*.md"))]
    offenders = [str(p.relative_to(REPO)) for p in places if "rw-vllm-dev-key" in p.read_text(errors="ignore")]
    assert offenders == []


def test_the_deploy_script_refuses_an_unset_key():
    """(1) The skill's documented raw `modal deploy` path must fail without a key, not deploy with a default."""
    import os
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items() if k != "VLLM_API_KEY"}
    code = f"import runpy; runpy.run_path({str(ms.QWEN_MODAL_SCRIPT)!r})"
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert out.returncode != 0 and "VLLM_API_KEY" in out.stderr


async def test_registration_records_the_servers_concurrency_ceiling(modal_fake):
    """(2) The ceiling travels with the model id, so the engine can check its own fan-out against it."""
    server = await amodel_server(ModelServerSpec(name="acme-qwen", max_num_seqs=48, api_key_env="ACME_VLLM_KEY"))
    assert PROFILES[server.model_id].max_concurrency == 48
    assert PROFILES["qwen3.8-27b-modal"].max_concurrency is None  # unknown for a provider-served model


async def test_reconnecting_to_an_undeployed_server_says_what_to_do(monkeypatch):
    """(4) Modal's own NotFoundError must not leak through the engine's public boundary."""
    import modal
    from modal.exception import NotFoundError

    from rag_wright.api import ModelServerNotDeployed

    class WebUrl:
        async def aio(self):
            raise NotFoundError("Lookup failed for Function 'serve' from the 'acme-qwen' app")

    monkeypatch.setattr(modal.Function, "from_name", lambda name, fn: types.SimpleNamespace(get_web_url=WebUrl()))
    with pytest.raises(ModelServerNotDeployed, match="acme-qwen.*adeploy_model_server"):
        await amodel_server(ModelServerSpec(name="acme-qwen", max_num_seqs=32))
    assert issubclass(ModelServerNotDeployed, RuntimeError)


def test_the_weights_volume_is_the_one_the_script_mounts():
    """(3) The engine reports cache state from the Volume the script writes the weights to; keep them the same."""
    assert f'modal.Volume.from_name("{ms.WEIGHTS_VOLUME}"' in ms.QWEN_MODAL_SCRIPT.read_text()


@pytest.fixture
def volume_fake(monkeypatch):
    import modal

    holdings = {"hub": ["hub/models--Qwen--Qwen3.8-27B-FP8", "hub/.locks"],
                "hub/models--Qwen--Qwen3.8-27B-FP8/snapshots": ["hub/models--Qwen--Qwen3.8-27B-FP8/snapshots/abc"]}

    class ListDir:
        async def aio(self, path):
            return [types.SimpleNamespace(path=p) for p in holdings.get(path, [])]

    seen = []

    def from_name(name, **kw):
        seen.append(name)
        return types.SimpleNamespace(listdir=ListDir())

    monkeypatch.setattr(modal.Volume, "from_name", from_name)
    return seen


async def test_status_reports_deployed_running_healthy_and_cached(modal_fake, volume_fake):
    from rag_wright.api import amodel_server_status

    spec = ModelServerSpec(name="acme-qwen", max_num_seqs=32, api_key_env="ACME_VLLM_KEY")
    first, second = await amodel_server_status(spec), await amodel_server_status(spec)
    assert (first.deployed, first.running, first.healthy, first.weights_cached) == (True, True, False, True)  # starting
    assert second.healthy  # the fake answers on its second poll
    assert set(volume_fake) == {ms.WEIGHTS_VOLUME} and modal_fake["cli"] == []  # it only looks; it deploys nothing


async def test_status_never_wakes_a_scaled_down_server(modal_fake, volume_fake):
    """A request to a scaled-to-zero server would start a cold start (and bill the GPU), so /health is not asked."""
    from rag_wright.api import amodel_server_status

    modal_fake["runners"] = 0
    status = await amodel_server_status(ModelServerSpec(name="acme-qwen", max_num_seqs=32, api_key_env="ACME_VLLM_KEY"))
    assert (status.deployed, status.running, status.healthy) == (True, False, False)
    assert modal_fake["health"] == 0


async def test_status_of_an_undeployed_server_with_other_weights_cached(modal_fake, volume_fake, monkeypatch):
    from rag_wright.api import amodel_server_status

    async def not_deployed(name):
        raise ms.ModelServerNotDeployed(name)

    monkeypatch.setattr(ms, "_web_url", not_deployed)
    status = await amodel_server_status(ModelServerSpec(name="acme-qwen", max_num_seqs=32,
                                                        model="Qwen/Qwen3.8-27B"))  # the bf16 weights: not cached
    assert (status.deployed, status.running, status.healthy, status.weights_cached) == (False, False, False, False)


async def test_astop_stops_the_modal_app(modal_fake):
    from rag_wright.api import astop_model_server

    await astop_model_server("acme-qwen")
    assert modal_fake["cli"][0][0] == ["app", "stop", "acme-qwen", "--yes"]
