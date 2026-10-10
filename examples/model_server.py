"""Bring up a self-hosted Qwen server through the engine, use it as a workspace's model, and bring it down.

Everything goes through `rag_wright.api`. The server is the locked setup of the `qwen-vllm-modal` skill (ADR-0110);
the walk-through is docs/configuration.md, "A self-hosted model server (Qwen on Modal)".

Prerequisites (see docs/installation.md):
  - `uv add 'rag-wright[modal]'` and a Modal login (`uv run modal token new`).
  - VLLM_API_KEY in .env: the server's API key (any long random string; there is no default).
  - ArcadeDB running locally, with ARCADEDB_* set in .env.

Run from the repo root:
    uv run python examples/model_server.py deploy      # status, deploy, ask one question, stop, confirm it is gone
    uv run python examples/model_server.py reconnect   # reconnect to a server already deployed, ask one question

`deploy` is what an operator script or startup job does, and it always stops the server at the end (an A100 bills
while it runs). `reconnect` is what every service process does at startup: it never deploys, and it leaves the
server running. A cold start takes minutes; progress is printed as `i/N`.
"""

from __future__ import annotations

import asyncio
import sys
import time

from dotenv import load_dotenv

from rag_wright.api import (
    EngineConfig,
    EvidenceItem,
    ModelRole,
    ModelServer,
    ModelServerNotDeployed,
    ModelServerSpec,
    StoreConfig,
    adeploy_model_server,
    agenerate_answer,
    amodel_server,
    amodel_server_status,
    await_model_server,
    measure_usage,
    open_workspace,
    stop_model_server,
)

# The same spec in every process: the name is the Modal app (and so the URL); max_num_seqs is the concurrency target.
SPEC = ModelServerSpec(name="rw-qwen3-example", max_num_seqs=16)


async def ask_one_question(server: ModelServer) -> None:
    """Use the server like any model: name it for a role in the workspace's config, then call the engine."""
    config = EngineConfig(store=StoreConfig.from_env(), models={ModelRole.GENERAL.value: server.model_id})
    ws = open_workspace(config, corpus="model_server_example")
    with measure_usage() as usage:
        out = await agenerate_answer(
            "How long is the warranty?",
            [EvidenceItem(chunk_id="c1", text="The warranty period is 24 months from delivery.")],
            ws=ws,
        )
    print(f"answer: {out.answer!r}")
    print(f"metered on: {sorted(usage.by_model)} ({usage.calls} call)")
    if sorted(usage.by_model) != [server.model_id]:
        raise SystemExit(f"expected the call to be metered on {server.model_id}")


async def deploy() -> None:
    before = await amodel_server_status(SPEC)  # looks only: deploys nothing, wakes nothing
    print(f"before: deployed={before.deployed} running={before.running} weights_cached={before.weights_cached} "
          f"({'a short image build and a cold start' if before.weights_cached else 'plus a ~27 GB weight download'})")
    start = time.monotonic()
    try:
        server = await adeploy_model_server(SPEC, poll_s=20)  # deploys, waits for /health, registers the model id
        print(f"ready in {time.monotonic() - start:.0f}s: model id {server.model_id}, endpoint {server.base_url}")
        await ask_one_question(server)
    finally:
        stop_model_server(SPEC.name)  # always, even if something above failed
    after = await amodel_server_status(SPEC)
    if after.deployed:
        raise SystemExit(f"{SPEC.name} is still deployed after stop")
    print(f"stopped: {SPEC.name} is no longer deployed (weights still cached: {after.weights_cached}); "
          f"{time.monotonic() - start:.0f}s total")


async def reconnect() -> None:
    try:
        server = await amodel_server(SPEC)  # looks the server up and registers its model id; does not deploy
    except ModelServerNotDeployed as exc:
        raise SystemExit(str(exc))
    try:  # a startup-sized timeout: on expiry, fail readiness (or fall back to a provider-served model id)
        await await_model_server(server, timeout_s=600, poll_s=20)
    except TimeoutError as exc:
        raise SystemExit(f"{exc}; failing startup")
    await ask_one_question(server)


if __name__ == "__main__":
    load_dotenv()  # VLLM_API_KEY and ARCADEDB_* from .env
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in ("deploy", "reconnect"):
        raise SystemExit("usage: uv run python examples/model_server.py deploy|reconnect")
    asyncio.run(deploy() if mode == "deploy" else reconnect())
