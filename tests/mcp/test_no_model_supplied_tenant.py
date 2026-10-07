"""issue 0035: THE PRINCIPLE, enforced. No MCP tool may expose a tenant/database/scope as a model-supplied
argument, and the store is resolved from caller-established session context (never a model argument, never a
per-process env in a multi-tenant deployment). This test fails the build if any server violates the rule --
cheap across four servers, and it holds automatically for every server added after them.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil

import pytest

import rag_wright.packs.compliance.mcp
import rag_wright.packs.contracts.mcp
from rag_wright.packs.contracts.mcp.session_store import (
    FORBIDDEN_TENANT_ARGS,
    assert_no_tenant_arguments,
    resolve_request_store,
)

# issue 0035 follow-up: the guard's server list is DERIVED, never hand-maintained. A server that forgets to
# register with the guard cannot exist -- forgetting is the failure, not the exemption. So the check holds for
# server #5 that does not exist yet, which is the whole point of adopting a principle now.

_BUILDER_CONFIG_PARAMS = frozenset({"store_resolver", "env_store", "name"})  # not injected capability fns


async def _stub(*_a, **_k):  # a runner stub for any injected fn -- never CALLED (the guard only reads schemas)
    return None


def _discover_servers():
    """Every `*_server` module in each pack's `mcp` package (ING-8c: the servers live in the contracts and the
    compliance packs) and its `build_*_mcp` builders, discovered -- not listed. Each server MUST expose at least
    one builder (else the guard could not check it, which is itself a failure)."""
    found = []
    for pkg in (rag_wright.packs.contracts.mcp, rag_wright.packs.compliance.mcp):
        for info in pkgutil.iter_modules(pkg.__path__):
            if info.name.endswith("_server"):
                found.append((info.name, _builders(importlib.import_module(f"{pkg.__name__}.{info.name}"))))
    return found


def _builders(module):
    """A server module's `build_*_mcp` builders (at least one, or the guard cannot check it)."""
    name = module.__name__.rsplit(".", 1)[-1]
    builders = [getattr(module, n) for n in dir(module)
                if n.startswith("build_") and n.endswith("_mcp") and inspect.isfunction(getattr(module, n))]
    assert builders, f"{name} exposes no build_*_mcp -- the tenant guard cannot check it"
    return builders


def _build_with_stubs(builder):
    """Build a server for introspection: pass a stub for every injected-fn parameter (required AND optional, so
    optional tools like compliance's generic/document checkers register too), leaving the config params
    (store_resolver / env_store / name) at their defaults."""
    kwargs = {p.name: _stub for p in inspect.signature(builder).parameters.values()
              if p.name not in _BUILDER_CONFIG_PARAMS}
    return builder(**kwargs)


def test_every_discovered_mcp_server_is_free_of_model_supplied_tenant_args():
    servers = _discover_servers()
    assert {n for n, _ in servers} >= {  # sanity: discovery actually found the known four (never fewer)
        "typed_property_retrieval_server", "intra_document_qa_server",
        "relational_qa_server", "compliance_server"}, servers
    for module_name, builders in servers:
        for builder in builders:
            offenders = assert_no_tenant_arguments(_build_with_stubs(builder))
            assert offenders == [], f"{module_name}.{builder.__name__} exposes model-supplied tenant arg(s): {offenders}"


def test_the_guard_would_catch_a_leak():
    # a server WITH a tenant argument must be flagged -- proves the guard actually bites
    from fastmcp import FastMCP

    bad: FastMCP = FastMCP(name="bad")

    @bad.tool(name="leaky", description="d")
    async def leaky(query: str, database: str = "") -> dict:  # the dangerous shape the rule forbids
        return {}

    assert "leaky.database" in assert_no_tenant_arguments(bad)


# --- the seam: the store is resolved out-of-band, never from a model argument -------------------------------

async def test_resolver_is_called_with_the_request_context(monkeypatch):
    seen = {}

    class _CtxStore:  # a marker "store" for the resolved tenant
        pass

    def _resolver(ctx):
        seen["ctx"] = ctx
        return _CtxStore()

    monkeypatch.setattr("fastmcp.server.dependencies.get_context", lambda: "the-request-context")
    store = await resolve_request_store(_resolver, env_store=None)
    assert isinstance(store, _CtxStore) and seen["ctx"] == "the-request-context"


async def test_async_resolver_is_awaited(monkeypatch):
    class _S:
        pass

    async def _aresolver(ctx):
        return _S()

    monkeypatch.setattr("fastmcp.server.dependencies.get_context", lambda: object())
    assert isinstance(await resolve_request_store(_aresolver, env_store=None), _S)


async def test_env_fallback_when_no_resolver():
    sentinel = object()
    assert await resolve_request_store(None, env_store=sentinel) is sentinel          # a value
    assert await resolve_request_store(None, env_store=lambda: sentinel) is sentinel  # or a factory


def test_forbidden_set_covers_the_obvious_names():
    assert {"database", "tenant", "scope", "db"} <= FORBIDDEN_TENANT_ARGS
