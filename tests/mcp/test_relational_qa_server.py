"""MCP-PROTO Phase B (B2): the relational_qa MCP-tool server. Hermetic -- FastMCP's in-memory Client calls the
tool with an injected stub qa_fn, so no ArcadeDB, no LLM, no transport/subprocess. Proves the wrapper exposes
the A2 leg as a well-formed MCP tool (name, schema, structured return) and that the qa_fn is injectable."""

from __future__ import annotations

import asyncio

from fastmcp import Client

from rag_wright.capabilities.answer_generator import GeneratedAnswer
from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.mcp.relational_qa_server import (
    build_relational_qa_mcp,
    demo_qa_fn,
    register_relational_qa_mcp,
)


def _call(mcp, args: dict):
    async def _run():
        async with Client(mcp) as client:
            tools = await client.list_tools()
            result = await client.call_tool("answer_relational_question", args)
            return [t.name for t in tools], result

    return asyncio.run(_run())


def test_tool_is_registered_with_the_expected_name():
    names, _ = _call(build_relational_qa_mcp(demo_qa_fn()),
                     {"query": "Which parties does AcmeCorp contract with?", "start_entity_id": "ent:acme"})
    assert "answer_relational_question" in names


def test_demo_qa_returns_a_real_shaped_cited_answer():
    _, result = _call(build_relational_qa_mcp(demo_qa_fn()),
                      {"query": "Which parties does AcmeCorp contract with?", "start_entity_id": "ent:acme"})
    data = result.data  # the tool's structured output (the GeneratedAnswer dict)
    assert data["abstained"] is False
    assert data["citations"]  # cited by the source contract(s) (graph-structural evidence)
    assert all(cid in data["answer"] for cid in data["citations"])


def test_registers_as_an_ard_mcp_tool():
    reg = CapabilityRegistry()
    register_relational_qa_mcp(reg)
    entry = reg.get("relational_qa_mcp")
    assert entry.kind == "mcp_tool" and entry.contract is GeneratedAnswer


def test_qa_fn_is_injectable_no_store_or_llm_needed():
    seen = {}

    async def stub(store, query: str, start_entity_id: str, max_hops: int) -> GeneratedAnswer:  # 0035: store-param
        seen["args"] = (query, start_entity_id, max_hops)
        return GeneratedAnswer(answer="Acme contracts with Beta (per K) [K:1:ab].",
                               citations=["K:1:ab"], abstained=False)

    _, result = _call(build_relational_qa_mcp(stub),
                      {"query": "partners?", "start_entity_id": "ent:acme", "max_hops": 2})
    assert seen["args"] == ("partners?", "ent:acme", 2)
    assert result.data["citations"] == ["K:1:ab"] and result.data["abstained"] is False
