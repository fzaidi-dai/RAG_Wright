"""MCP-PROTO Phase B (B3): the typed_property_retrieval MCP-tool server. Hermetic -- FastMCP's in-memory Client
calls the tool with an injected stub retrieval_fn, so no ArcadeDB, no encoders, no LLM. Proves the wrapper
exposes Leg B as a well-formed MCP tool (name, schema, structured return) and that the retrieval_fn is injectable.

Note: unlike B1/B2 (GeneratedAnswer), this leg's contract is TypedPropertyRetrieval (query + ranked spans)."""

from __future__ import annotations

import asyncio

from fastmcp import Client

from rag_wright.capabilities.property_boosted_retrieval import RankedSpan
from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.mcp.typed_property_retrieval_server import (
    build_typed_property_retrieval_mcp,
    demo_retrieval_fn,
    register_typed_property_retrieval_mcp,
)
from rag_wright.subgraphs.typed_property_retrieval import TypedPropertyRetrieval


def _call(mcp, args: dict):
    async def _run():
        async with Client(mcp) as client:
            tools = await client.list_tools()
            result = await client.call_tool("retrieve_typed_property_spans", args)
            return [t.name for t in tools], result

    return asyncio.run(_run())


def test_tool_is_registered_with_the_expected_name():
    names, _ = _call(build_typed_property_retrieval_mcp(demo_retrieval_fn()),
                     {"query": "cap on liability set at a multiple of the fees paid"})
    assert "retrieve_typed_property_spans" in names


def test_demo_retrieval_returns_real_shaped_ranked_cited_spans():
    _, result = _call(build_typed_property_retrieval_mcp(demo_retrieval_fn()),
                      {"query": "cap on liability set at a multiple of the fees paid"})
    data = result.data  # the tool's structured output (the TypedPropertyRetrieval dict)
    assert data["query"]
    assert data["results"] and all(r["span_id"] and r["rank"] for r in data["results"])  # cited + ranked
    # ranks are 1-based and ordered
    assert [r["rank"] for r in data["results"]] == sorted(r["rank"] for r in data["results"])


def test_registers_as_an_ard_mcp_tool():
    reg = CapabilityRegistry()
    register_typed_property_retrieval_mcp(reg)
    entry = reg.get("typed_property_retrieval_mcp")
    assert entry.kind == "mcp_tool" and entry.contract is TypedPropertyRetrieval


def test_retrieval_fn_is_injectable_no_store_or_encoders_needed():
    seen = {}

    async def stub(query: str) -> TypedPropertyRetrieval:
        seen["query"] = query
        return TypedPropertyRetrieval(query=query, results=[RankedSpan(
            span_id="K:5:cafe01", text="liability capped at 2x fees", function="Cap On Liability",
            match_score=0.9, matched=[("cap_multiple", "2x")], rank=1)])

    _, result = _call(build_typed_property_retrieval_mcp(stub), {"query": "fee-multiple cap"})
    assert seen["query"] == "fee-multiple cap"
    assert result.data["results"][0]["span_id"] == "K:5:cafe01"
