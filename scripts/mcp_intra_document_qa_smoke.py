"""MCP-PROTO Phase B real-infra smoke: the PRODUCTION intra_document_qa MCP server end-to-end (no demo fn).

Unlike the hermetic tests (injected stub) and the Deep-Agent demo (RAG_MCP_DEMO=1 stub), this spawns the REAL
server over stdio -- production_qa_fn wires the real ArcadeDB clause KG + real generation -- and calls the tool
with a REAL contract_id, so it proves the wrapped capability actually works as a tool against live infra, not
just that the wrapper is well-formed.

Substrate: the env-selected ArcadeDB (`.env` -> localhost:2480 `ragwright_cuad_full`) + OpenRouter models. The
GENERAL role (classifier + generation) is overridden to the eval-validated Gemma-4-31b (Granite-8B is weak on
the silver set), pinned to Cerebras (fast, no fallback) -- passed to the server subprocess via env.

  uv run --no-sync python -m scripts.mcp_intra_document_qa_smoke [CONTRACT_ID]
"""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv

_DEFAULT_CONTRACT = "LIMEENERGYCO_09_09_1999-EX-10-DISTRIBUTOR_AGREEMENT"  # has a Cap On Liability clause
_QUESTION = "How is liability capped in this contract, and under what conditions?"


async def _main() -> None:
    load_dotenv()
    from fastmcp import Client

    contract_id = sys.argv[1] if len(sys.argv) > 1 else _DEFAULT_CONTRACT

    # env for the REAL server subprocess: inherit .env (ARCADEDB_*, OPENROUTER_API_KEY) + force the validated
    # Gemma-4 for the GENERAL role (classifier + generation) pinned to Cerebras. NO RAG_MCP_DEMO -> real fn.
    server_env = {
        **os.environ,
        "RAG_SERVING": "openrouter",
        "RAG_MODEL_GENERAL": "google/gemma-4-31b-it",
        "OPENROUTER_PROVIDER": "Cerebras",
        "OPENROUTER_ALLOW_FALLBACKS": "false",
    }
    client = Client({"mcpServers": {"intra_document_qa": {
        "command": sys.executable,
        "args": ["-m", "rag_wright.mcp.intra_document_qa_server"],
        "env": server_env,
    }}})

    print(f"[smoke] spawning PRODUCTION intra_document_qa server (real KG) | contract={contract_id}", flush=True)
    async with client:
        tools = await client.list_tools()
        print(f"[smoke] tools: {[t.name for t in tools]}", flush=True)
        print(f"[smoke] Q: {_QUESTION}", flush=True)
        result = await client.call_tool(
            "answer_contract_question", {"contract_id": contract_id, "question": _QUESTION})

    data = result.data
    print(f"\n[smoke] answer_kind: {data.get('answer_kind')} | abstained: {data['abstained']}", flush=True)
    print(f"[smoke] citations ({len(data['citations'])}): {[c[-20:] for c in data['citations'][:6]]}", flush=True)
    print(f"[smoke] answer:\n{data['answer'][:700]}", flush=True)
    ok = (not data["abstained"]) and bool(data["citations"])
    print(f"\n[smoke] {'OK -- real cited answer over MCP' if ok else 'CHECK -- abstained / uncited'}", flush=True)


if __name__ == "__main__":
    asyncio.run(_main())
