"""MCP-PROTO Phase B (B1): the intra_document_qa MCP-tool server. Hermetic -- FastMCP's in-memory Client calls
the tool with an injected stub qa_fn, so no ArcadeDB, no LLM, no transport/subprocess. Proves the wrapper
exposes the A1 leg as a well-formed MCP tool (name, schema, structured return) and that the qa_fn is injectable."""

from __future__ import annotations

import asyncio

from fastmcp import Client

from rag_wright.capabilities.answer_generator import GeneratedAnswer
from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.mcp.intra_document_qa_server import (
    build_intra_document_qa_mcp,
    demo_qa_fn,
    register_intra_document_qa_mcp,
)


def _call(mcp, args: dict):
    async def _run():
        async with Client(mcp) as client:
            tools = await client.list_tools()
            result = await client.call_tool("answer_contract_question", args)
            return [t.name for t in tools], result

    return asyncio.run(_run())


def test_tool_is_registered_with_the_expected_name():
    names, _ = _call(build_intra_document_qa_mcp(demo_qa_fn()),
                     {"contract_id": "AcmeMSA", "question": "How is liability capped?"})
    assert "answer_contract_question" in names


def test_demo_qa_returns_a_real_shaped_cited_answer():
    _, result = _call(build_intra_document_qa_mcp(demo_qa_fn()),
                      {"contract_id": "AcmeMSA", "question": "How is liability capped?"})
    data = result.data  # the tool's structured output (the GeneratedAnswer dict)
    assert data["abstained"] is False
    assert data["citations"]  # no claim without a citation (FR-Q.6)
    # every cited id appears inline in the answer body (grounded, cited)
    assert all(cid in data["answer"] for cid in data["citations"])


def test_registers_as_an_ard_mcp_tool():
    # MCP-PROTO: the MCP-tool surface is a distinct ARD entry (mcp_tool), same contract as the subgraph
    reg = CapabilityRegistry()
    register_intra_document_qa_mcp(reg)
    entry = reg.get("intra_document_qa_mcp")
    assert entry.kind == "mcp_tool" and entry.contract is GeneratedAnswer


def test_qa_fn_is_injectable_no_store_or_llm_needed():
    # a custom stub proves the MCP surface is decoupled from ArcadeDB/models -- the whole point of the wrapper
    seen = {}

    def stub(contract_id: str, question: str) -> GeneratedAnswer:
        seen["args"] = (contract_id, question)
        return GeneratedAnswer(answer="Capped at 2x fees [C:1:ab].", citations=["C:1:ab"], abstained=False)

    _, result = _call(build_intra_document_qa_mcp(stub), {"contract_id": "C", "question": "cap?"})
    assert seen["args"] == ("C", "cap?")
    assert result.data["citations"] == ["C:1:ab"] and result.data["abstained"] is False
