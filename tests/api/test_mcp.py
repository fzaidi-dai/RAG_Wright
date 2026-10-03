"""EP-RT-2 (ADR-0117): the GENERIC capability->MCP adapter. One function turns any catalogued invokable capability
(subgraph/model) into a FastMCP server, driven entirely by its ARD manifest -- no bespoke per-capability server code.

Hermetic: FastMCP's in-memory Client calls the tool; the invoker's adapter is stubbed (monkeypatched into
`api.invoke.capability_impl`), so the full path (index lookup -> kind validation -> invoker ->
JSON) runs with no ArcadeDB / encoders / LLM. Mirrors the bespoke servers' in-memory test pattern."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from fastmcp import Client

from rag_wright.api import EngineConfig, StoreConfig, WorkspaceHandle
from rag_wright.api import invoke as _invoke
from rag_wright.api.mcp import build_capability_mcp


def _handle():
    return WorkspaceHandle(store=object(),
                           config=EngineConfig(store=StoreConfig(host="h", port="1", user="u", password="p")),
                           corpus="c")


def _call(mcp, tool_name: str, args: dict):
    async def _run():
        async with Client(mcp) as client:
            tools = await client.list_tools()
            result = await client.call_tool(tool_name, args)
            return {t.name: t for t in tools}, result

    return asyncio.run(_run())


def test_subgraph_capability_exposed_as_an_mcp_tool_named_for_the_capability(monkeypatch):
    seen = {}

    async def _stub(resources, inputs):
        seen["inputs"] = inputs
        return {"retrieval": {"query": inputs["query"], "results": []}}

    monkeypatch.setattr(_invoke, "capability_impl", lambda name: _stub)  # EP-CORE-2: impl resolved via capability_impl
    tools, result = _call(build_capability_mcp("typed_property_retrieval", resources=_handle()),
                          "typed_property_retrieval", {"inputs": {"query": "liability cap"}})
    assert "typed_property_retrieval" in tools
    assert tools["typed_property_retrieval"].description  # the ARD manifest description is the tool description
    assert seen["inputs"] == {"query": "liability cap"}       # inputs dispatched straight to the invoker
    assert result.data["result"]["retrieval"]["query"] == "liability cap"  # subgraph state under the uniform envelope


def test_model_capability_exposed_as_an_mcp_tool(monkeypatch):
    def _stub(resources, inputs):
        return [{"dimension": "dispute_method", "value": "ARBITRATION", "confidence": "EXTRACTED"}]

    monkeypatch.setattr(_invoke, "capability_impl", lambda name: _stub)  # EP-CORE-2: impl resolved via capability_impl
    _, result = _call(build_capability_mcp("clause_property_classification", resources=_handle()),
                      "clause_property_classification", {"inputs": {"text": "...", "functions": ["Dispute Resolution"]}})
    assert result.data["result"][0]["dimension"] == "dispute_method"  # the model's soft tags under the envelope


def test_rejects_a_non_invokable_kind():
    with pytest.raises(ValueError):  # agent_skill has no invoker adapter -> not exposable generically
        build_capability_mcp("generation", resources=_handle())
    with pytest.raises(ValueError):  # an mcp_tool is already an MCP tool
        build_capability_mcp("typed_property_retrieval_mcp", resources=_handle())


def test_rejects_an_unknown_capability():
    with pytest.raises(KeyError):
        build_capability_mcp("not_a_capability", resources=_handle())


# --- live: the generic adapter over the REAL 29-dim fleet through the in-memory MCP client (no ArcadeDB needed) ---

def _fleet_present() -> bool:
    """True only when every model dir the fleet references has been fetched (gitignored / local-only)."""
    cfg_path = Path("src/rag_wright/spans/dim_fleet.json")
    models_dir = Path(os.getenv("RAG_DIM_MODELS_DIR", "data/models"))
    try:
        cfg = json.loads(cfg_path.read_text())
    except OSError:
        return False
    for spec in cfg.values():
        sub = "laya" if spec["framework"] == "laya" else "setfit"
        if not (models_dir / sub / spec["model"]).exists():
            return False
    return True


@pytest.mark.fleet  # loads the LOCAL 20-model property fleet (multi-GB RSS) -> opt-in, out of the default run
@pytest.mark.skipif(not _fleet_present(), reason="29-dim fleet checkpoints not present (gitignored / local-only)")
def test_generic_mcp_tool_runs_the_real_fleet_end_to_end():
    """EP-RT-2 live: a catalogued model capability, exposed generically as an MCP tool with zero bespoke code, runs
    the real fleet through the invoker and returns real soft tags over the MCP protocol."""
    _, result = _call(
        build_capability_mcp("clause_property_classification", resources=_handle()),
        "clause_property_classification",
        {"inputs": {"text": ("Any dispute arising out of this Agreement shall be finally settled by binding "
                             "arbitration administered by the American Arbitration Association."),
                    "functions": ["Dispute Resolution"]}})
    tags = result.data["result"]
    assert tags and all(set(t) == {"dimension", "value", "confidence"} for t in tags)
    dispute = [t for t in tags if t["dimension"] == "dispute_method"]
    assert dispute and all(str(t["value"]).lower() != "none" for t in dispute), (
        f"dispute_method did not fire through the generic MCP tool; got {[(t['dimension'], t['value']) for t in tags]}")
