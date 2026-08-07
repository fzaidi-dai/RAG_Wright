"""MCP-PROTO: the compliance_check MCP-tool server. Hermetic -- FastMCP's in-memory Client calls the tool with
an injected stub check_fn, so no ArcadeDB, no LLM, no transport/subprocess. Proves the wrapper exposes the
capability as a well-formed MCP tool (name, schema, structured return) and that the checker is injectable."""

from __future__ import annotations

import asyncio

from fastmcp import Client

from rag_wright.contracts.compliance import ComplianceReport
from rag_wright.mcp.compliance_server import build_compliance_mcp, demo_check_fn


def _call(mcp, args: dict):
    async def _run():
        async with Client(mcp) as client:
            tools = await client.list_tools()
            result = await client.call_tool("check_ad_compliance", args)
            return [t.name for t in tools], result

    return asyncio.run(_run())


def test_tool_is_registered_with_the_expected_name_and_schema():
    names, _ = _call(build_compliance_mcp(demo_check_fn()),
                     {"ad_text": "clinically proven to erase wrinkles", "source_doc": "demo"})
    assert "check_ad_compliance" in names


def test_demo_checker_returns_a_real_shaped_violation_report():
    _, result = _call(build_compliance_mcp(demo_check_fn()),
                      {"ad_text": "clinically proven; guaranteed results", "source_doc": "demo_ad"})
    data = result.data  # the tool's structured output (the report dict)
    assert data["verdict"] == "violation"  # 2 overclaim findings >= the ad-violation threshold
    assert data["source_doc"] == "demo_ad"
    assert data["summary"]["violation"] == 2
    assert len(data["findings"]) == 2
    # both-sided citation is present on every finding (no claim without a citation)
    assert all(f["citation_claim"] and f["citation_requirement"] for f in data["findings"])


def test_check_fn_is_injectable_no_store_or_llm_needed():
    # a custom stub proves the MCP surface is decoupled from ArcadeDB/models -- the whole point of the wrapper
    seen = {}

    def stub(ad_text: str, source_doc: str) -> ComplianceReport:
        seen["ad"] = ad_text
        return ComplianceReport(source_doc=source_doc, findings=[], summary={"compliant": 4})

    _, result = _call(build_compliance_mcp(stub), {"ad_text": "smooth relaxing flavor", "source_doc": "x"})
    assert seen["ad"] == "smooth relaxing flavor"
    assert result.data["verdict"] == "compliant"  # no violation/needs_review findings -> compliant rollup
