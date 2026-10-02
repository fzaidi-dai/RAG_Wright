"""EP-RT-2 (ADR-0117): the generic capability->MCP adapter.

One function, `build_capability_mcp(slug, *, resources)`, exposes ANY catalogued invokable capability as a FastMCP
server with NO bespoke per-capability server code -- the tool's name/title/description are read from the ARD
manifest, and its handler dispatches through the engine invoker (`ainvoke_subgraph`/`invoke_model`) over the bound
workspace. This is the zero-boilerplate path: a new-domain product (or a new engine capability) gets a discoverable
MCP tool for free, the moment the capability is in the ARD catalog.

The four hand-written servers in `rag_wright/mcp/` stay: they offer a CURATED, typed tool signature + description for
the Tier-1 legs. This generic adapter is the complement -- it takes a single opaque `inputs` dict (the capability's
own input contract, dispatched straight to the invoker) rather than a per-capability typed signature, because the
ARD manifest carries no JSON input schema to derive one from.

Only `subgraph` and `model` kinds are supported (the kinds the invoker can dispatch). An `mcp_tool` is already an
MCP tool; `function`/`agent_skill` have no invoker adapter yet (EP-API-2c) -- all rejected with a clear error.

Store binding is server-side via the `WorkspaceHandle` (issue 0035): the calling agent never supplies a tenant or
store; the workspace is bound when the server is built. Multi-tenant request routing is a product concern (a product
opens one workspace per corpus; `corpus` = the backend db name)."""
from __future__ import annotations

import asyncio
from typing import Any

from fastmcp import FastMCP

from rag_wright.api.invoke import _index, ainvoke_subgraph, invoke_model
from rag_wright.api.workspace import WorkspaceHandle

_INVOKABLE_KINDS = ("subgraph", "model")


def _jsonable(obj: Any) -> Any:
    """Normalize an invoker result (a LangGraph state dict, a Pydantic model, or a list of them) to JSON-safe
    data -- the structured content the calling agent receives."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    return obj


def build_capability_mcp(slug: str, *, resources: WorkspaceHandle, name: str | None = None) -> FastMCP:
    """Build a FastMCP server exposing the catalogued capability `slug` as one MCP tool, driven by its ARD manifest.

    The tool is named for the capability (its canonical slug -- stable + discoverable), titled with the manifest
    display name, and described by the manifest description. It takes one `inputs` dict (the capability's input
    contract) and dispatches through the engine invoker over `resources` (the bound workspace), returning the result
    as JSON. Only `subgraph`/`model` kinds are supported.

    Raises `KeyError` for an unknown slug; `ValueError` for a non-invokable kind."""
    idx = _index()
    if slug not in idx:
        raise KeyError(f"unknown capability {slug!r} (not in the ARD catalog)")
    spec = idx[slug]
    if spec.kind not in _INVOKABLE_KINDS:
        raise ValueError(
            f"capability {slug!r} is kind {spec.kind!r}; only {_INVOKABLE_KINDS} are exposable via the generic "
            f"MCP adapter (an mcp_tool is already an MCP tool; function/agent-skill have no invoker adapter yet)")

    mcp: FastMCP = FastMCP(
        name=name or f"rag-wright-{slug.replace('_', '-')}",
        instructions=(
            f"{spec.description}\n\nCall `{slug}` with an `inputs` object carrying the capability's inputs. "
            f"Example queries: {'; '.join(spec.representative_queries)}."),
    )
    kind = spec.kind

    @mcp.tool(name=slug, title=spec.display_name, description=spec.description)
    async def _invoke_capability(inputs: dict) -> dict:
        """Invoke the capability over the bound workspace.

        Args:
            inputs: The capability's input contract (e.g. {"query": ...} for a retrieval leg, or
                {"text": ..., "functions": [...]} for a classifier). Dispatched straight to the engine invoker.

        Returns:
            {"result": <the capability's output as JSON>} -- a uniform envelope (a subgraph's state dict or a
            model's soft-tag list both land under `result`), since the output contract varies by capability.
        """
        if kind == "subgraph":
            out = await ainvoke_subgraph(slug, inputs, resources=resources)
        else:  # model -- sync invoker; offload so the event loop is not blocked by local inference
            out = await asyncio.to_thread(invoke_model, slug, inputs, resources=resources)
        return {"result": _jsonable(out)}

    return mcp


def serve_capability_mcp(slug: str, *, resources: WorkspaceHandle, transport: str = "stdio") -> None:
    """Serve one catalogued capability as an MCP tool over `transport` (default stdio, so an agent can spawn it)."""
    build_capability_mcp(slug, resources=resources).run(transport=transport)
