"""PS-19: deploy the self-hosted Qwen server and use it like any other model.

The setup is the `qwen-vllm-modal` skill's locked one (ADR-0110, Config B): `ModelServerSpec`'s defaults are that
setup, and deploying runs the script shipped with that skill (`modal deploy`, the command the skill documents) with
those settings, then waits for the server's `/health`. Each server registers a model profile with its own endpoint
(copied from the `qwen3.8-27b-modal` profile, so the Qwen thinking settings carry over), so `server.model_id` goes in
`EngineConfig.models` or any `model=` argument; no `VLLM_BASE_URL` is needed. The API key comes from the environment
variable the spec names; there is no default key. Needs `modal` (`rag-wright[modal]`) and a Modal login.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from rag_wright.models.profiles import PROFILES, register_model_profile

QWEN_MODAL_SCRIPT = (Path(__file__).resolve().parents[1] / ".agents" / "skills" / "qwen-vllm-modal" / "scripts"
                     / "modal_qwen3_vllm_server.py")
_TEMPLATE_PROFILE = "qwen3.8-27b-modal"
_SERVE_FUNCTION = "serve"  # the web function in the script; its URL is the server's root


@dataclass(frozen=True)
class ModelServerSpec:
    """One self-hosted Qwen server on Modal. The defaults are the locked setup of the `qwen-vllm-modal` skill
    (ADR-0110, Config B): FP8 weights + FP8 KV cache, one A100-80GB, tensor parallel 1, 16K context, 0.95 GPU memory,
    served as `Qwen/Qwen3.8-27B` with the hermes tool parser and the qwen3 reasoning parser, image builder 2025.06.
    `max_num_seqs` has no default: ADR-0110 sets it to the real concurrency target (keep client concurrency below it).
    `name` is the Modal app name, and so the server's URL. `api_key_env` names the environment variable that holds
    the server's API key."""

    max_num_seqs: int
    name: str = "rw-qwen3-modal"
    model: str = "Qwen/Qwen3.8-27B-FP8"
    kv_cache_dtype: str = "fp8"
    served_name: str = "Qwen/Qwen3.8-27B"
    gpu: str = "A100-80GB:1"
    tensor_parallel: int = 1
    max_len: int = 16384
    gpu_util: float = 0.95
    tool_parser: str = "hermes"
    reasoning_parser: str = "qwen3"
    image_builder_version: str = "2025.06"
    api_key_env: str = "VLLM_API_KEY"

    def deploy_env(self) -> dict[str, str]:
        """The deploy command's settings, as the skill's documented command passes them."""
        return {
            "APP_NAME": self.name, "MODEL": self.model, "KV_CACHE_DTYPE": self.kv_cache_dtype,
            "SERVED_NAME": self.served_name, "TOOL_PARSER": self.tool_parser,
            "REASONING_PARSER": self.reasoning_parser, "GPU": self.gpu, "TP": str(self.tensor_parallel),
            "MAX_LEN": str(self.max_len), "GPU_UTIL": str(self.gpu_util), "MAX_NUM_SEQS": str(self.max_num_seqs),
            "MODAL_IMAGE_BUILDER_VERSION": self.image_builder_version,
        }


@dataclass(frozen=True)
class ModelServer:
    """A deployed server: `model_id` is the model id to use (in `EngineConfig.models` or a `model=` argument);
    `base_url` is its OpenAI-compatible endpoint; `root` is its URL (for `/health`)."""

    name: str
    root: str
    base_url: str
    model_id: str
    served_model_id: str
    api_key_env: str


def _modal_cli(args: list[str], env: dict[str, str]) -> None:
    """Run Modal's CLI (`modal deploy ...`, `modal app stop ...`) with the current interpreter."""
    subprocess.run([sys.executable, "-m", "modal", *args], env=env, check=True)


async def _web_url(name: str) -> str:
    import modal

    url = await modal.Function.from_name(name, _SERVE_FUNCTION).get_web_url.aio()  # the async form: called in a loop
    if not url:
        raise RuntimeError(f"Modal app {name!r} has no web endpoint (is it deployed?)")
    return url.rstrip("/")


def _healthy(root: str, key: Optional[str]) -> bool:
    import httpx

    try:
        return httpx.get(f"{root}/health", headers={"Authorization": f"Bearer {key}"} if key else None,
                         timeout=30).status_code == 200
    except httpx.HTTPError:
        return False


def _require_modal() -> None:
    try:
        import modal  # noqa: F401
    except ImportError as exc:
        raise ImportError("deploying a model server needs `modal`: install `rag-wright[modal]` and log in to Modal "
                          "(`modal token new`)") from exc


def _register(spec: ModelServerSpec, root: str) -> ModelServer:
    model_id = f"{_TEMPLATE_PROFILE}@{spec.name}"
    register_model_profile(PROFILES[_TEMPLATE_PROFILE].model_copy(update={
        "model_id": model_id, "backend": "vllm", "served_model_id": spec.served_name,
        "base_url": f"{root}/v1", "base_url_env": None, "api_key_env": spec.api_key_env}))
    return ModelServer(name=spec.name, root=root, base_url=f"{root}/v1", model_id=model_id,
                       served_model_id=spec.served_name, api_key_env=spec.api_key_env)


async def await_model_server(server: ModelServer, *, timeout_s: float = 1500, poll_s: float = 15,
                             progress: Callable[[str], object] = print) -> ModelServer:
    """Wait until the server answers `/health` (a cold start is about 8 minutes, ADR-0109); progress as `i/N`.
    Raises `TimeoutError` after `timeout_s`."""
    key = os.environ.get(server.api_key_env)
    polls = max(1, int(timeout_s // poll_s)) if poll_s else "?"
    start, i = time.monotonic(), 0
    while True:
        i += 1
        if await asyncio.to_thread(_healthy, server.root, key):
            progress(f"[model-server] {server.name} ready after {time.monotonic() - start:.0f}s")
            return server
        elapsed = time.monotonic() - start
        if elapsed >= timeout_s:
            raise TimeoutError(f"model server {server.name!r} not ready after {elapsed:.0f}s")
        progress(f"[model-server] waiting for {server.name} {i}/{polls} ({elapsed:.0f}s)")
        await asyncio.sleep(poll_s)


async def adeploy_model_server(spec: ModelServerSpec, *, wait: bool = True, timeout_s: float = 1500,
                               poll_s: float = 15, progress: Callable[[str], object] = print) -> ModelServer:
    """Deploy (or redeploy) the server described by `spec` on Modal, register its model profile, and (with `wait`)
    wait for it to be ready. Returns the `ModelServer`; use `server.model_id` as the model. Raises `RuntimeError` when
    the API-key variable is unset (nothing is deployed). The server runs (and bills) until `stop_model_server`, or
    until Modal scales it down after 10 idle minutes."""
    key = os.environ.get(spec.api_key_env)
    if not key:
        raise RuntimeError(f"set {spec.api_key_env} to the server's API key before deploying")
    _require_modal()
    progress(f"[model-server] deploying {spec.name} ({spec.model}, {spec.gpu}, max_num_seqs={spec.max_num_seqs})")
    env = {**os.environ, **spec.deploy_env(), "VLLM_API_KEY": key}
    await asyncio.to_thread(_modal_cli, ["deploy", str(QWEN_MODAL_SCRIPT)], env)
    server = _register(spec, await _web_url(spec.name))
    progress(f"[model-server] deployed {spec.name} at {server.base_url}; model id {server.model_id}")
    if wait:
        await await_model_server(server, timeout_s=timeout_s, poll_s=poll_s, progress=progress)
    return server


async def amodel_server(spec: ModelServerSpec) -> ModelServer:
    """Reconnect to a server deployed earlier (by this process or another) without redeploying: look up its URL and
    register its model profile. `spec` must match the deployed server (its name and served model)."""
    _require_modal()
    return _register(spec, await _web_url(spec.name))


def stop_model_server(name: str) -> None:
    """Stop the Modal app `name` (stops billing); its URL stops answering."""
    _require_modal()
    _modal_cli(["app", "stop", name, "--yes"], dict(os.environ))
