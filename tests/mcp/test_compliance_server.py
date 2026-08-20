"""MCP-PROTO: the compliance_check MCP-tool server. Hermetic -- FastMCP's in-memory Client calls the tool with
an injected stub check_fn, so no ArcadeDB, no LLM, no transport/subprocess. Proves the wrapper exposes the
capability as a well-formed MCP tool (name, schema, structured return) and that the checker is injectable."""

from __future__ import annotations

import asyncio

from fastmcp import Client

from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.contracts.compliance import ComplianceReport
from rag_wright.mcp.compliance_server import (
    build_compliance_mcp,
    demo_check_fn,
    register_compliance_check_mcp,
)


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


def test_registers_as_an_ard_mcp_tool():
    # MCP-PROTO: the MCP-tool surface is a distinct ARD entry (mcp_tool), same contract as the subgraph
    reg = CapabilityRegistry()
    register_compliance_check_mcp(reg)
    entry = reg.get("compliance_check_mcp")
    assert entry.kind == "mcp_tool" and entry.contract is ComplianceReport


def test_check_fn_is_injectable_no_store_or_llm_needed():
    # a custom stub proves the MCP surface is decoupled from ArcadeDB/models -- the whole point of the wrapper
    seen = {}

    async def stub(ad_text: str, source_doc: str, sources=None) -> ComplianceReport:
        seen["ad"] = ad_text
        return ComplianceReport(source_doc=source_doc, findings=[], summary={"compliant": 4})

    _, result = _call(build_compliance_mcp(stub), {"ad_text": "smooth relaxing flavor", "source_doc": "x"})
    assert seen["ad"] == "smooth relaxing flavor"
    assert result.data["verdict"] == "compliant"  # no violation/needs_review findings -> compliant rollup


def test_generic_check_compliance_tool_is_exposed_and_returns_a_noted_report():
    # COMP-VERDICT-GENERIC: the domain-agnostic tool is added when a generic_check_fn is provided
    from rag_wright.contracts.compliance import ComplianceFinding, ComplianceReport, Verdict

    async def _generic(subject_text, source_doc, sources=None):
        return ComplianceReport(
            source_doc=source_doc,
            findings=[ComplianceFinding(
                claim_id="f0", requirement_id="osha:1904.4", verdict=Verdict.VIOLATION,
                rationale="not recorded", citation_claim=f"{source_doc}: subject",
                citation_requirement="§ 1904.4", confidence=0.9)],
            summary={"violation": 1}, gap_matrix=[])

    mcp = build_compliance_mcp(demo_check_fn(), generic_check_fn=_generic)

    async def _run():
        async with Client(mcp) as client:
            tools = [t.name for t in await client.list_tools()]
            result = await client.call_tool(
                "check_compliance", {"subject_text": "employer did not log an injury", "source_doc": "osha_case"})
            return tools, result.data

    tools, data = asyncio.run(_run())
    assert "check_compliance" in tools and "check_ad_compliance" in tools  # both exposed
    assert data["source_doc"] == "osha_case" and data["summary"]["violation"] == 1
    assert data["verdict"] == "needs_review"  # ad-level rollup: 1 violation finding -> needs_review (threshold >=2)
    assert "note" in data and "enrich" in data["note"].lower()  # suggests domain enrichment


def test_generic_tool_absent_when_no_generic_check_fn():
    mcp = build_compliance_mcp(demo_check_fn())  # no generic_check_fn

    async def _run():
        async with Client(mcp) as client:
            return [t.name for t in await client.list_tools()]

    tools = asyncio.run(_run())
    assert "check_compliance" not in tools and "check_ad_compliance" in tools


def test_check_compliance_tool_forwards_sources_and_exposes_the_param():
    # issue 0007: the MCP tool must expose the SAME `sources` scoping the function gained -- not left store-wide
    from rag_wright.contracts.compliance import ComplianceReport

    seen = {}

    async def _generic(subject_text, source_doc, sources=None):
        seen["sources"] = sources
        return ComplianceReport(source_doc=source_doc, findings=[], summary={"compliant": 1})

    mcp = build_compliance_mcp(demo_check_fn(), generic_check_fn=_generic)

    async def _run():
        async with Client(mcp) as client:
            tool = {t.name: t for t in await client.list_tools()}["check_compliance"]
            result = await client.call_tool(
                "check_compliance", {"subject_text": "x", "source_doc": "s", "sources": ["p1", "p2"]})
            return tool, result.data

    tool, data = asyncio.run(_run())
    assert seen["sources"] == ["p1", "p2"]                       # scoping reaches the checker
    assert "sources" in tool.inputSchema["properties"]           # and is exposed on the tool's input schema
    assert data["source_doc"] == "s"


def test_check_ad_compliance_tool_forwards_sources():
    # symmetry: the advertising tool exposes the same parameter (harmless None default)
    from rag_wright.contracts.compliance import ComplianceReport

    seen = {}

    async def stub(ad_text, source_doc, sources=None):
        seen["sources"] = sources
        return ComplianceReport(source_doc=source_doc, findings=[], summary={"compliant": 1})

    _call(build_compliance_mcp(stub), {"ad_text": "x", "source_doc": "s", "sources": ["ftc-255"]})
    assert seen["sources"] == ["ftc-255"]


def test_sources_defaults_to_none_when_omitted():
    # back-compat: omitting `sources` keeps store-wide semantics (None reaches the checker)
    from rag_wright.contracts.compliance import ComplianceReport

    seen = {}

    async def stub(ad_text, source_doc, sources=None):
        seen["sources"] = sources
        return ComplianceReport(source_doc=source_doc, findings=[], summary={"compliant": 1})

    _call(build_compliance_mcp(stub), {"ad_text": "x", "source_doc": "s"})
    assert seen["sources"] is None


def test_check_compliance_document_tool_decodes_base64_and_forwards():
    # issue 0008: the document tool accepts base64 bytes (JSON can't carry raw bytes), decodes + forwards them
    import base64

    from rag_wright.contracts.compliance import ComplianceReport

    seen = {}

    async def _doc_check(doc_name, data, sources=None):
        seen.update(doc_name=doc_name, data=data, sources=sources)
        return ComplianceReport(source_doc=doc_name, findings=[], summary={"compliant": 1})

    mcp = build_compliance_mcp(demo_check_fn(), document_check_fn=_doc_check)
    raw = b"%PDF-1.4 real subject bytes"
    b64 = base64.b64encode(raw).decode()

    async def _run():
        async with Client(mcp) as client:
            tool = {t.name: t for t in await client.list_tools()}["check_compliance_document"]
            result = await client.call_tool(
                "check_compliance_document",
                {"doc_name": "subject.pdf", "data_base64": b64, "sources": ["p1"]})
            return tool, result.data

    tool, data = asyncio.run(_run())
    assert seen["doc_name"] == "subject.pdf" and seen["data"] == raw and seen["sources"] == ["p1"]  # decoded bytes
    assert "data_base64" in tool.inputSchema["properties"]                                          # exposed on schema
    assert data["source_doc"] == "subject.pdf"


def test_document_tool_absent_when_no_document_check_fn():
    mcp = build_compliance_mcp(demo_check_fn())  # no document_check_fn

    async def _run():
        async with Client(mcp) as client:
            return [t.name for t in await client.list_tools()]

    tools = asyncio.run(_run())
    assert "check_compliance_document" not in tools and "check_ad_compliance" in tools
