"""MCP-PROTO Phase B (B2): the `relational_qa` MCP-tool server (FastMCP).

Wraps the `relational_qa` SUBGRAPH (A2) as a single MCP tool
`answer_relational_question(query, start_entity_id, max_hops)` -> a cited `GeneratedAnswer` (as JSON). The store
(the entity/relationship graph) + the generation model (via the seam) bind SERVER-SIDE from env, so the tool
call is just `{query, start_entity_id}` -- the token/coordination win. Evidence is GRAPH-STRUCTURAL (each reached
entity cited by its source contract; no chunk text), so a relational answer is verifiable by contract id. Third
Tier-1 leg wrapped, mirroring the `compliance_server.py` / `intra_document_qa_server.py` reference pattern.

Grounded (library rule): FastMCP `server.py:L278` (`FastMCP(name, instructions, version=...)`), `@mcp.tool`,
`run(transport=...)` -- confirmed against the cloned-repo AST graph (`graphify-out/framework/graph.json`) + the
installed 3.4.6 signatures, same surface the reference servers use.

`build_relational_qa_mcp(qa_fn)` injects the answerer so the server is hermetically testable (a stub `qa_fn`, no
ArcadeDB/LLM). `main()` picks the production answerer (real subgraph, env-wired) or a deterministic demo answerer
(`RAG_MCP_DEMO=1`, no infra) and serves over stdio (so a Deep Agent can spawn it).

  # real (needs the entity graph + a model backend via env, like scripts/phase_a_leg_validate.py):
  uv run --no-sync python -m rag_wright.mcp.relational_qa_server
  # demo (no infra -- deterministic answer; for the Deep-Agent prototype):
  RAG_MCP_DEMO=1 uv run --no-sync python -m rag_wright.mcp.relational_qa_server
"""

from __future__ import annotations

import os
from typing import Any, Callable

from fastmcp import FastMCP

from rag_wright.capabilities.answer_generator import GeneratedAnswer

# qa_fn: (query, start_entity_id, max_hops) -> GeneratedAnswer. Injected so the server is testable without infra.
RelationalQAFn = Callable[[str, str, int], GeneratedAnswer]

_TOOL_DESCRIPTION = (
    "Answer a relational question about a known entity by traversing the contract entity graph, returning a "
    "grounded, cited answer. From a start entity, follows relationship edges (default: contracting parties) up "
    "to `max_hops`, and generates the answer from the reached graph structure ALONE: each fact is cited by the "
    "SOURCE CONTRACT it came from (no chunk text), and a question the graph does not support yields an "
    "abstention (abstained=true), never a fabrication. Use for 'which parties does X contract with' style "
    "questions when the start entity is known."
)


def _answer_to_dict(answer: GeneratedAnswer) -> dict[str, Any]:
    """The tool's JSON payload: the cited answer. This is the structured content the calling agent receives."""
    return answer.model_dump(mode="json")


def build_relational_qa_mcp(qa_fn: RelationalQAFn, *, name: str = "rag-wright-relational-qa") -> FastMCP:
    """Build the FastMCP server exposing `relational_qa` as one tool. `qa_fn` is injected (real subgraph in
    production; a stub in tests) so the MCP surface is testable with no ArcadeDB / LLM."""
    mcp: FastMCP = FastMCP(
        name=name,
        instructions=(
            "Relational question-answering over the contract entity graph. Use answer_relational_question to "
            "answer a question about a known entity's relationships with cited, graph-structural evidence."
        ),
    )

    @mcp.tool(name="answer_relational_question", description=_TOOL_DESCRIPTION)
    def answer_relational_question(query: str, start_entity_id: str, max_hops: int = 1) -> dict[str, Any]:
        """Answer one relational question by traversing the entity graph from a start entity.

        Args:
            query: The natural-language relational question (e.g. "which parties does X contract with?").
            start_entity_id: The entity_id to traverse from (must be in the graph).
            max_hops: Traversal depth. Defaults to 1 (direct relationships).

        Returns:
            A cited answer: {answer, citations[], abstained}. `citations` are the SOURCE CONTRACT ids the facts
            came from; `abstained` is true when the graph does not support an answer.
        """
        return _answer_to_dict(qa_fn(query, start_entity_id, max_hops))

    return mcp


# --- production answerer: the real relational_qa subgraph, env-wired (like scripts/phase_a_leg_validate.py) -----


def production_qa_fn(*, answer_model_id: str | None = None) -> RelationalQAFn:
    """Wire the real `relational_qa` over the env-selected store + model: ArcadeDB entity graph (`ARCADEDB_*`,
    `QA_DB`), the GENERAL model for generation (via the seam; `RAG_SERVING`). Generation goes through
    `answer_model_for` so it takes the right path per ADR-0045 (client-side tag-parse). Heavy imports are lazy so
    `RAG_MCP_DEMO` never pays for them."""
    from dotenv import load_dotenv

    load_dotenv()
    from rag_wright.capabilities.answer_generator import answer_model_for
    from rag_wright.models.profiles import ModelRole, model_for
    from rag_wright.store.arcadedb import ArcadeDBStore
    from rag_wright.subgraphs.relational_qa import production_relational_qa

    store = ArcadeDBStore.from_env(database=os.environ.get("QA_DB", "ragwright_cuad_full"))
    answer_model = answer_model_for(answer_model_id or model_for(ModelRole.GENERAL))
    leg = production_relational_qa(store=store, answer_model=answer_model)

    def _qa(query: str, start_entity_id: str, max_hops: int) -> GeneratedAnswer:
        out = leg.invoke({"query": query, "start_entity_id": start_entity_id, "max_hops": max_hops})
        ans = out.get("answer")
        if ans is None:  # defensive: no answer produced -> an honest abstention, never a fabrication
            return GeneratedAnswer(
                answer="The entity graph could not be traversed for this question.",
                citations=[], abstained=True)
        return ans

    return _qa


# --- demo answerer: a deterministic, real-shaped cited answer (no ArcadeDB / LLM) for the Deep-Agent prototype -


def demo_qa_fn() -> RelationalQAFn:
    """A deterministic stub with the REAL contract shape -- a cited, graph-structural relational answer. Lets the
    Deep-Agent prototype (and the hermetic test) exercise the full MCP path with no ArcadeDB / LLM."""
    _cid = "AcmeBetaMSA:3:beef0002"

    def _qa(query: str, start_entity_id: str, max_hops: int) -> GeneratedAnswer:
        return GeneratedAnswer(
            answer=f"AcmeCorp contracts with BetaLLC (per AcmeBetaMSA) [{_cid}].",
            citations=[_cid], abstained=False)

    return _qa


def register_relational_qa_mcp(registry) -> None:
    """Register `relational_qa_mcp` (MCP-PROTO Phase B): the ARD `mcp_tool` surface of the `relational_qa`
    subgraph -- the same capability exposed as a discoverable, cross-agent MCP tool (`answer_relational_question`,
    served by `rag_wright.mcp.relational_qa_server`) so an agent can call it as ONE tool via ARD search instead of
    embedding the subgraph. Distinct ARD identity from the in-process `relational_qa` subgraph; same output
    contract `GeneratedAnswer`."""
    registry.register(
        "relational_qa_mcp",
        contract=GeneratedAnswer,
        kind="mcp_tool",
        display_name="Relational contract QA (MCP tool)",
    )


def main() -> None:
    """Serve the relational_qa MCP tool over stdio. `RAG_MCP_DEMO=1` uses the no-infra demo answerer."""
    qa_fn = demo_qa_fn() if os.environ.get("RAG_MCP_DEMO") == "1" else production_qa_fn()
    build_relational_qa_mcp(qa_fn).run(transport="stdio")


if __name__ == "__main__":
    main()
