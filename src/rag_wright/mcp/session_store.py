"""issue 0035: the seam that lets an MCP tool be multi-tenant-safe.

THE PRINCIPLE: an MCP tool NEVER takes a tenant, database, or scope as a model-supplied argument. A tool's
arguments are filled by a language model, so a tenant argument puts a cross-tenant read one token-prediction
away. Instead the store is resolved from **session context the CALLER establishes out-of-band** (MCP
initialization params / transport headers), never from a model argument and never from a per-process env var
in a multi-tenant deployment.

The engine does NOT implement tenancy (that is product policy). It provides this seam: a `build_*_mcp` factory
accepts an optional `store_resolver`, and per request resolves the store from the caller-populated MCP request
context. When no resolver is wired, it falls back to a single process-wide store -- correct for a demo, an
eval, or a single-tenant deployment, and still with no model-supplied tenant.
"""

from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable, Optional, Union

from rag_wright.store.seam import Store

# The caller binds a store per SESSION: given the opaque per-request MCP context (whatever tenancy the caller
# set out-of-band -- init params / headers), return that tenant's store. Sync or async. It is handed the MCP
# request context, NEVER a model-supplied value.
StoreResolver = Callable[[Any], Union[Store, Awaitable[Store]]]

# argument names an MCP tool must never expose to the model (the guard test asserts none appear in any schema).
FORBIDDEN_TENANT_ARGS = frozenset({"database", "db", "tenant", "scope", "corpus", "workspace", "customer"})


async def resolve_request_store(
    store_resolver: Optional[StoreResolver], env_store: Union[Store, Callable[[], Store], None]
) -> Optional[Store]:
    """The per-request store, resolved WITHOUT any model-supplied argument.

    - `store_resolver` given (multi-tenant): resolve against the CURRENT MCP request context (`get_context()`),
      so the caller binds tenancy per session. The context is opaque to the engine -- the caller reads its own
      tenancy from it. Awaited if the resolver is async.
    - `store_resolver` None (single-tenant / demo / eval): return `env_store` (a store, or a factory returning
      one), the process-wide fallback. Still never a model argument.
    """
    if store_resolver is not None:
        from fastmcp.server.dependencies import get_context  # the current request's context (caller-populated)

        ctx = get_context()
        out = store_resolver(ctx)
        return await out if inspect.isawaitable(out) else out
    return env_store() if callable(env_store) else env_store


def assert_no_tenant_arguments(mcp: Any) -> list[str]:
    """Return the offending `tool.arg` names if any MCP tool exposes a model-supplied tenant/database/scope
    argument (issue 0035); an empty list means the server is safe. The guard test fails the build on any hit;
    a caller may also call it on its own servers. Model-facing arguments are the tool's input-schema
    `properties`; a `get_context()`-injected context is never in that schema, so it is correctly ignored."""
    import asyncio

    tools = asyncio.run(mcp.list_tools())
    offenders: list[str] = []
    for tool in tools:
        props = set((getattr(tool, "parameters", {}) or {}).get("properties", {}))
        for bad in sorted(props & FORBIDDEN_TENANT_ARGS):
            offenders.append(f"{tool.name}.{bad}")
    return offenders
