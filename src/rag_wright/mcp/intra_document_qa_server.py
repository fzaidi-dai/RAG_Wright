"""MCP-PROTO Phase B (B1): the `intra_document_qa` MCP-tool server (FastMCP).

Wraps the `intra_document_qa` SUBGRAPH (A1, Leg A) as a single MCP tool
`answer_contract_question(contract_id, question)` -> a cited `GeneratedAnswer` (as JSON). The store (the contract
clause KG) + the models (function classifier + Gemma generation via the seam) bind SERVER-SIDE from env, so the
tool call is just `{contract_id, question}` -- the token/coordination win. An external agent discovers this via
ARD search and calls it. Second Tier-1 leg wrapped after `compliance_check` (`compliance_server.py`, the
reference pattern this mirrors).

Grounded (library rule): FastMCP `server.py:L278` (`FastMCP(name, instructions, version=...)`), `@mcp.tool`,
`run(transport=...)` -- confirmed against the cloned-repo AST graph (`graphify-out/framework/graph.json`) + the
installed 3.4.6 signatures, same surface the reference server uses.

`build_intra_document_qa_mcp(qa_fn)` injects the answerer so the server is hermetically testable (a stub
`qa_fn`, no ArcadeDB/LLM). `main()` picks the production answerer (real subgraph, env-wired) or a deterministic
demo answerer (`RAG_MCP_DEMO=1`, no infra) and serves over stdio (so a Deep Agent can spawn it).

  # real (needs the contract KG + a model backend via env, like scripts/phase_a_leg_validate.py):
  uv run --no-sync python -m rag_wright.mcp.intra_document_qa_server
  # demo (no infra -- deterministic answer; for the Deep-Agent prototype):
  RAG_MCP_DEMO=1 uv run --no-sync python -m rag_wright.mcp.intra_document_qa_server
"""

from __future__ import annotations

import os
from typing import Any, Callable

from fastmcp import FastMCP

from rag_wright.capabilities.answer_generator import GeneratedAnswer

# qa_fn: (contract_id, question) -> GeneratedAnswer. Injected so the server is testable without infra.
QAFn = Callable[[str, str], GeneratedAnswer]

_TOOL_DESCRIPTION = (
    "Answer a natural-language question about ONE known contract from its clause knowledge graph, returning a "
    "grounded, cited answer. Classifies the question to its clause function(s), serves those clauses (with any "
    "cap carve-outs), and generates the answer from that evidence ALONE: every claim cites the chunk_id(s) it "
    "rests on, and a question the contract does not support yields an abstention (abstained=true), never a "
    "fabrication. Use when the contract is already identified and the user asks about its terms."
)


def _answer_to_dict(answer: GeneratedAnswer) -> dict[str, Any]:
    """The tool's JSON payload: the cited answer. This is the structured content the calling agent receives."""
    return answer.model_dump(mode="json")


def build_intra_document_qa_mcp(qa_fn: QAFn, *, name: str = "rag-wright-intra-document-qa") -> FastMCP:
    """Build the FastMCP server exposing `intra_document_qa` as one tool. `qa_fn` is injected (real subgraph in
    production; a stub in tests) so the MCP surface is testable with no ArcadeDB / LLM."""
    mcp: FastMCP = FastMCP(
        name=name,
        instructions=(
            "Contract question-answering over a per-contract clause knowledge graph. Use "
            "answer_contract_question to answer a question about a known contract with cited evidence."
        ),
    )

    @mcp.tool(name="answer_contract_question", description=_TOOL_DESCRIPTION)
    def answer_contract_question(contract_id: str, question: str) -> dict[str, Any]:
        """Answer one question about a known contract from its clause KG.

        Args:
            contract_id: The identifier of the contract to query (its clauses must be in the KG).
            question: The natural-language question about that contract's terms.

        Returns:
            A cited answer: {answer, citations[], abstained}. `citations` are chunk_ids present in the evidence;
            `abstained` is true when the contract does not support an answer.
        """
        return _answer_to_dict(qa_fn(contract_id, question))

    return mcp


# --- production answerer: the real intra_document_qa subgraph, env-wired (like scripts/phase_a_leg_validate.py) -


def production_qa_fn(*, function_model_id: str | None = None, answer_model_id: str | None = None) -> QAFn:
    """Wire the real `intra_document_qa` over the env-selected store + models: ArcadeDB contract KG (`ARCADEDB_*`,
    `QA_DB`), the GENERAL model for generation (via the seam; `RAG_SERVING`). ADR-0047: there is no function
    classifier anymore -- the leg serves the whole contract; `function_model_id` is kept as a back-compat alias
    for the generation-model default. Heavy imports are lazy so `RAG_MCP_DEMO` never pays for them."""
    from dotenv import load_dotenv

    load_dotenv()
    from rag_wright.models.profiles import ModelRole, model_for
    from rag_wright.store.arcadedb import ArcadeDBStore
    from rag_wright.subgraphs.intra_document_qa import production_intra_document_qa

    store = ArcadeDBStore.from_env(database=os.environ.get("QA_DB", "ragwright_cuad_full"))
    default_model = answer_model_id or function_model_id or model_for(ModelRole.GENERAL)
    leg = production_intra_document_qa(store=store, answer_model_id=default_model)

    def _qa(contract_id: str, question: str) -> GeneratedAnswer:
        out = leg.invoke({"contract_id": contract_id, "question": question})
        ans = out.get("answer")
        if ans is None:  # a pipeline dead-letter (e.g. orphan span) -> an honest abstention, never a fabrication
            return GeneratedAnswer(
                answer="The contract knowledge graph could not be queried for this question.",
                citations=[], abstained=True)
        return ans

    return _qa


# --- demo answerer: a deterministic, real-shaped cited answer (no ArcadeDB / LLM) for the Deep-Agent prototype -


def demo_qa_fn() -> QAFn:
    """A deterministic stub with the REAL contract shape -- a cited, grounded cap answer. Lets the Deep-Agent
    prototype (and the hermetic test) exercise the full MCP path with no ArcadeDB / LLM."""
    _cid = "AcmeMSA:12:deadbeef01"

    def _qa(contract_id: str, question: str) -> GeneratedAnswer:
        return GeneratedAnswer(
            answer=f"Seller's aggregate liability is capped at two times (2x) the fees paid in the "
                   f"preceding 12 months [{_cid}].",
            citations=[_cid], abstained=False)

    return _qa


def register_intra_document_qa_mcp(registry) -> None:
    """Register `intra_document_qa_mcp` (MCP-PROTO Phase B): the ARD `mcp_tool` surface of the
    `intra_document_qa` subgraph -- the same capability exposed as a discoverable, cross-agent MCP tool
    (`answer_contract_question`, served by `rag_wright.mcp.intra_document_qa_server`) so an agent can call it as
    ONE tool via ARD search instead of embedding the subgraph. Distinct ARD identity from the in-process
    `intra_document_qa` subgraph; same output contract `GeneratedAnswer`."""
    registry.register(
        "intra_document_qa_mcp",
        contract=GeneratedAnswer,
        kind="mcp_tool",
        display_name="Intra-document contract QA (MCP tool)",
    )


def main() -> None:
    """Serve the intra_document_qa MCP tool over stdio. `RAG_MCP_DEMO=1` uses the no-infra demo answerer."""
    qa_fn = demo_qa_fn() if os.environ.get("RAG_MCP_DEMO") == "1" else production_qa_fn()
    build_intra_document_qa_mcp(qa_fn).run(transport="stdio")


if __name__ == "__main__":
    main()
