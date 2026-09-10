"""issue 0035: THE PRINCIPLE, enforced. No MCP tool may expose a tenant/database/scope as a model-supplied
argument, and the store is resolved from caller-established session context (never a model argument, never a
per-process env in a multi-tenant deployment). This test fails the build if any server violates the rule --
cheap across four servers, and it holds automatically for every server added after them.
"""

from __future__ import annotations

import asyncio

import pytest

from rag_wright.mcp import (
    compliance_server,
    intra_document_qa_server,
    relational_qa_server,
    typed_property_retrieval_server,
)
from rag_wright.mcp.session_store import (
    FORBIDDEN_TENANT_ARGS,
    assert_no_tenant_arguments,
    resolve_request_store,
)


def _all_servers():
    """Every MCP server, built with the no-infra demo/stub runners (so this is hermetic)."""
    return {
        "typed_property_retrieval": typed_property_retrieval_server.build_typed_property_retrieval_mcp(
            typed_property_retrieval_server.demo_retrieval_fn()),
        "intra_document_qa": intra_document_qa_server.build_intra_document_qa_mcp(
            intra_document_qa_server.demo_qa_fn()),
        "relational_qa": relational_qa_server.build_relational_qa_mcp(
            relational_qa_server.demo_qa_fn()),
        # compliance: build ALL THREE tools so the guard covers each
        "compliance": compliance_server.build_compliance_mcp(
            compliance_server.demo_check_fn(),
            generic_check_fn=compliance_server.demo_check_fn(),
            document_check_fn=_demo_document_check_fn()),
    }


async def _demo_document_check_fn(*_a, **_k):  # a trivial DocumentCheckFn so check_compliance_document registers
    return None


def test_no_mcp_tool_exposes_a_model_supplied_tenant_argument():
    for name, mcp in _all_servers().items():
        offenders = assert_no_tenant_arguments(mcp)
        assert offenders == [], f"{name} server exposes model-supplied tenant arg(s): {offenders}"


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
