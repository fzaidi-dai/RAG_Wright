"""MCP-PROTO Phase B prototype test: a simple Deep Agent that uses the THREE query-leg MCP servers as TOOLS.

The parity check for MCP-B1/B2/B3 (matching scripts/mcp_compliance_agent_demo.py for the compliance tool):
proves an agent discovers each capability and calls it as ONE MCP tool -- instead of embedding the subgraph and
shuttling its intermediate state -- for all three Tier-1 query legs at once:
  - intra_document_qa      -> answer_contract_question(contract_id, question)   -> cited GeneratedAnswer
  - relational_qa          -> answer_relational_question(query, start_entity_id) -> cited GeneratedAnswer
  - typed_property_retrieval-> retrieve_typed_property_spans(query)              -> ranked cited spans

Wiring (grounded against the installed libs + the cloned FastMCP AST graph):
  - each MCP server is spawned over stdio by langchain-mcp-adapters' MultiServerMCPClient (RAG_MCP_DEMO=1 -> the
    no-infra demo fns, so the TOOLS need no ArcadeDB/encoders/LLM);
  - `client.get_tools()` adapts the MCP tools into LangChain tools;
  - `create_deep_agent(model, tools=[...])` (deepagents) gets them; the agent's driving model is the seam's
    model (RAG_SERVING=openrouter dev path), the only real LLM the demo needs.

  RAG_SERVING=openrouter uv run --no-sync python -m scripts.mcp_query_legs_agent_demo
"""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv

_ASK = (
    "Use your tools to do all three of the following, calling one tool per task and then summarizing each "
    "cited result:\n"
    "1. What does contract 'AcmeMSA' say about how liability is capped?\n"
    "2. Which parties does entity 'ent:acme' contract with?\n"
    "3. Find clauses across the corpus that cap liability at a multiple of the fees paid."
)


def _server(module: str) -> dict:
    return {"transport": "stdio", "command": sys.executable, "args": ["-m", module],
            "env": {**os.environ, "RAG_MCP_DEMO": "1"}}  # demo fns: no ArcadeDB/encoders/LLM in the TOOLS


async def _main() -> None:
    load_dotenv()
    from langchain_mcp_adapters.client import MultiServerMCPClient

    from rag_wright.models.profiles import ModelRole, model_for
    from rag_wright.models.seam import build_model

    # 1. spawn the three query-leg MCP servers over stdio (demo fns -> no infra in the TOOLS)
    client = MultiServerMCPClient({
        "intra_document_qa": _server("rag_wright.packs.contracts.mcp.intra_document_qa_server"),
        "relational_qa": _server("rag_wright.packs.contracts.mcp.relational_qa_server"),
        "typed_property_retrieval": _server("rag_wright.packs.contracts.mcp.typed_property_retrieval_server"),
    })
    tools = await client.get_tools()
    print(f"[agent-demo] MCP tools discovered: {[t.name for t in tools]}", flush=True)

    # 2. a simple Deep Agent handed all three MCP tools
    from deepagents import create_deep_agent

    agent = create_deep_agent(
        model=build_model(model_for(ModelRole.STRUCTURED_REASONING)),  # dev path -> OpenRouter
        tools=tools,
        system_prompt=(
            "You are a contract-analysis assistant with three tools: answer_contract_question (a question about "
            "one known contract), answer_relational_question (which parties an entity contracts with), and "
            "retrieve_typed_property_spans (find clauses matching a typed condition across the corpus). Call the "
            "right tool for each task and rely on its cited result -- do not answer from your own knowledge. "
            "After the tool calls, summarize each answer and cite the ids the tool returned."
        ),
    )

    # 3. ask it to do all three -> it should call each MCP tool and summarize the cited results
    print("[agent-demo] asking the agent to run all three query legs...", flush=True)
    result = await agent.ainvoke({"messages": [{"role": "user", "content": _ASK}]})

    msgs = result["messages"]
    tool_calls = [tc["name"] for m in msgs for tc in getattr(m, "tool_calls", []) or []]
    print(f"[agent-demo] tool calls the agent made: {tool_calls}", flush=True)
    print("\n[agent-demo] === final answer ===\n" + msgs[-1].content, flush=True)


if __name__ == "__main__":
    asyncio.run(_main())
