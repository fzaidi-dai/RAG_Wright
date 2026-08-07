"""MCP-PROTO prototype test: a simple Deep Agent that uses the compliance MCP server as a TOOL.

Proves the target pattern: an agent discovers a capability (here wired directly; in production via an ARD
search tool) and calls it as ONE MCP tool -- `check_ad_compliance(ad_text)` -> a cited compliance report --
instead of embedding the compliance_check subgraph and shuttling its intermediate state. That is the context /
coordination saving.

Wiring (all grounded against the installed libs + the cloned FastMCP AST graph):
  - the MCP server (`rag_wright.mcp.compliance_server`) is spawned over stdio by langchain-mcp-adapters'
    MultiServerMCPClient (RAG_MCP_DEMO=1 -> the no-infra demo checker, so the TOOL needs no ArcadeDB/LLM);
  - `client.get_tools()` adapts the MCP tool into a LangChain tool;
  - `create_deep_agent(model, tools=[...])` (deepagents) gets it as a tool; the agent's driving model is the
    seam's model (RAG_SERVING=openrouter dev path -> Granite), the only real LLM the demo needs.

  RAG_SERVING=openrouter uv run --no-sync python -m scripts.mcp_compliance_agent_demo
"""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv

_AD = (
    "RadiantGlow Serum: clinically proven to erase deep wrinkles, and guaranteed to reverse aging in 7 days. "
    "Dermatologists everywhere agree it is the #1 anti-aging breakthrough."
)


async def _main() -> None:
    load_dotenv()
    from langchain_mcp_adapters.client import MultiServerMCPClient

    from rag_wright.models.profiles import ModelRole, model_for
    from rag_wright.models.seam import build_model

    # 1. spawn the compliance MCP server over stdio (demo checker: no ArcadeDB/LLM in the TOOL)
    server_env = {**os.environ, "RAG_MCP_DEMO": "1"}
    client = MultiServerMCPClient({
        "compliance": {
            "transport": "stdio",
            "command": sys.executable,
            "args": ["-m", "rag_wright.mcp.compliance_server"],
            "env": server_env,
        }
    })
    tools = await client.get_tools()
    print(f"[agent-demo] MCP tools discovered: {[t.name for t in tools]}", flush=True)

    # 2. a simple Deep Agent that is handed the MCP tool
    from deepagents import create_deep_agent

    agent = create_deep_agent(
        model=build_model(model_for(ModelRole.STRUCTURED_REASONING)),  # dev path -> OpenRouter Granite
        tools=tools,
        system_prompt=(
            "You are an ad-compliance assistant. When asked whether an advertisement is compliant, call the "
            "check_ad_compliance tool with the ad text, then summarize its verdict and cite the specific "
            "findings (each claim, its verdict, and the regulation clause). Do not judge the ad yourself -- "
            "rely on the tool's cited report."
        ),
    )

    # 3. ask it to screen an ad -> it should call the MCP tool and summarize the cited report
    print(f"[agent-demo] asking the agent to screen an ad ({len(_AD)} chars)...", flush=True)
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": f"Is this advertisement compliant?\n\n{_AD}"}]})

    msgs = result["messages"]
    tool_calls = [tc["name"] for m in msgs for tc in getattr(m, "tool_calls", []) or []]
    print(f"[agent-demo] tool calls the agent made: {tool_calls}", flush=True)
    print("\n[agent-demo] === final answer ===\n" + msgs[-1].content, flush=True)


if __name__ == "__main__":
    asyncio.run(_main())
