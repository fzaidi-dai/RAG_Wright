"""MCP-PROTO Phase B (B3): the `typed_property_retrieval` MCP-tool server (FastMCP).

Wraps the `typed_property_retrieval` SUBGRAPH (Leg B) as a single MCP tool `retrieve_typed_property_spans(query)`
-> a `TypedPropertyRetrieval` (the query + its property-boosted, cited ranked spans) as JSON. The store (the
clause KG) + the encoders/models (BGE + LegalBERT + granite constraint-extraction + the function classifier via
the seam) bind SERVER-SIDE from env, so the tool call is just `{query}` -- the token/coordination win. An
external agent discovers this via ARD search and calls it.

Unlike B1/B2 (which return a `GeneratedAnswer`), this leg's contract is `TypedPropertyRetrieval` -- corpus-wide
RETRIEVAL, not single-answer generation -- so the tool returns ranked cited spans, not a written answer. Fourth
and last Tier-1 leg wrapped, mirroring the `compliance_server.py` reference pattern.

Grounded (library rule): FastMCP `server.py:L278` (`FastMCP(name, instructions, version=...)`), `@mcp.tool`,
`run(transport=...)` -- confirmed against the cloned-repo AST graph (`graphify-out/framework/graph.json`) + the
installed 3.4.6 signatures, same surface the reference servers use.

`build_typed_property_retrieval_mcp(retrieval_fn)` injects the retriever so the server is hermetically testable
(a stub, no ArcadeDB/encoders/LLM). `main()` picks the production retriever (real subgraph, env-wired) or a
deterministic demo retriever (`RAG_MCP_DEMO=1`, no infra) and serves over stdio (so a Deep Agent can spawn it).

  # real (needs the clause KG + the encoders/models via env, like scripts/phase_a_leg_validate.py):
  uv run --no-sync python -m rag_wright.mcp.typed_property_retrieval_server
  # demo (no infra -- deterministic ranked spans; for the Deep-Agent prototype):
  RAG_MCP_DEMO=1 uv run --no-sync python -m rag_wright.mcp.typed_property_retrieval_server
"""

from __future__ import annotations

import os
from typing import Any, Awaitable, Callable

from fastmcp import FastMCP

from rag_wright.capabilities.property_boosted_retrieval import RankedSpan
from rag_wright.capabilities.span_relevance_judgment import RelevanceVerdict
from rag_wright.subgraphs.typed_property_retrieval import JudgedSpan, TypedPropertyRetrieval

# retrieval_fn: (query) -> TypedPropertyRetrieval. Injected so the server is testable without infra.
# ASYNC-C1 (ADR-0057): async -- the tool handler awaits it, and it awaits the async typed_property_retrieval subgraph.
RetrievalFn = Callable[[str], Awaitable[TypedPropertyRetrieval]]

_TOOL_DESCRIPTION = (
    "Retrieve the most relevant contract clauses for a query from across the corpus, property-boosted and "
    "cited. Extracts the query's typed (dimension, value) constraints and routes its clause function(s), then "
    "ranks a bounded semantic pool by constraint match, returning ranked spans each with its span_id citation, "
    "text, clause function, match score, and the constraints it satisfied. This is corpus-wide RETRIEVAL (ranked "
    "evidence), not a written answer. Use to find clauses matching a typed condition (e.g. 'cap on liability set "
    "at a multiple of the fees paid') across many contracts."
)


def _retrieval_to_dict(retrieval: TypedPropertyRetrieval) -> dict[str, Any]:
    """The tool's JSON payload: the query + its ranked cited spans. This is the structured content the calling
    agent receives."""
    return retrieval.model_dump(mode="json")


def build_typed_property_retrieval_mcp(
    retrieval_fn: RetrievalFn, *, name: str = "rag-wright-typed-property-retrieval"
) -> FastMCP:
    """Build the FastMCP server exposing `typed_property_retrieval` as one tool. `retrieval_fn` is injected (real
    subgraph in production; a stub in tests) so the MCP surface is testable with no ArcadeDB / encoders / LLM."""
    mcp: FastMCP = FastMCP(
        name=name,
        instructions=(
            "Corpus-wide, property-boosted clause retrieval over the contract KG. Use "
            "retrieve_typed_property_spans to find the clauses matching a typed condition, ranked and cited."
        ),
    )

    @mcp.tool(name="retrieve_typed_property_spans", description=_TOOL_DESCRIPTION)
    async def retrieve_typed_property_spans(query: str) -> dict[str, Any]:
        """Retrieve ranked, cited clause spans matching a query from across the corpus.

        Args:
            query: The retrieval query, ideally expressing a typed condition (a clause function and/or a
                property value, e.g. "cap on liability at a multiple of fees").

        Returns:
            {query, results[]} where each result is a ranked cited span WITH its relevance verdict (issue 0023):
            {span: {span_id, text, function, match_score, matched[], rank}, relevance: {verdict, rationale,
            confidence} | null}. `verdict` is relevant | not_relevant | uncertain; `relevance` is null only when no
            relevance judge is wired. Empty results when nothing matches.
        """
        return _retrieval_to_dict(await retrieval_fn(query))

    return mcp


# --- production retriever: the real Leg B subgraph, env-wired (like scripts/phase_a_leg_validate.py validate_leg_b) -


def production_retrieval_fn(*, k: int = 8) -> RetrievalFn:
    """Wire the real `typed_property_retrieval` over the env-selected store + encoders + models: ArcadeDB clause
    KG (`ARCADEDB_*`, `QA_DB`), BGE embedder (local or the A100 `STACK_URL` adapter), and granite
    constraint-extraction (via the seam; `RAG_SERVING`). ADR-0047: no function classifier -- whole-index pool.
    Heavy imports are lazy so `RAG_MCP_DEMO` never pays for them."""
    from dotenv import load_dotenv

    load_dotenv()
    from rag_wright.capabilities.dg_extraction import default_extraction_model
    from rag_wright.capabilities.remote_encoders import query_embedder
    from rag_wright.store.arcadedb import ArcadeDBStore
    from rag_wright.subgraphs.typed_property_retrieval import production_typed_property_retrieval

    store = ArcadeDBStore.from_env(database=os.environ.get("QA_DB", "ragwright_cuad_full"))
    leg = production_typed_property_retrieval(
        store=store, embedder=query_embedder(),
        extract_model=default_extraction_model("query-constraints", "ibm-granite/granite-4.2-8b"), k=k)

    async def _retrieve(query: str) -> TypedPropertyRetrieval:
        out = await leg.ainvoke({"query": query})
        return out["retrieval"]

    return _retrieve


# --- demo retriever: deterministic, real-shaped ranked cited spans (no infra) for the Deep-Agent prototype -----


def demo_retrieval_fn() -> RetrievalFn:
    """A deterministic stub with the REAL contract shape -- two ranked, cited, property-matched cap spans. Lets
    the Deep-Agent prototype (and the hermetic test) exercise the full MCP path with no ArcadeDB / encoders."""

    async def _retrieve(query: str) -> TypedPropertyRetrieval:
        results = [
            JudgedSpan(
                span=RankedSpan(
                    span_id="AcmeMSA:12:deadbeef01",
                    text="Seller's aggregate liability shall not exceed two times (2x) the fees paid.",
                    function="Cap On Liability", match_score=0.94, matched=[("cap_multiple", "2x")], rank=1),
                relevance=RelevanceVerdict(verdict="relevant", rationale="an express liability cap", confidence=0.95)),
            JudgedSpan(
                span=RankedSpan(
                    span_id="BetaSaaS:7:cafe0042",
                    text="In no event shall liability exceed the total fees paid in the prior 12 months.",
                    function="Cap On Liability", match_score=0.71, matched=[("cap_basis", "fees paid")], rank=2),
                relevance=RelevanceVerdict(verdict="relevant", rationale="a fees-paid liability cap", confidence=0.9)),
        ]
        return TypedPropertyRetrieval(query=query, results=results)

    return _retrieve


def register_typed_property_retrieval_mcp(registry) -> None:
    """Register `typed_property_retrieval_mcp` (MCP-PROTO Phase B): the ARD `mcp_tool` surface of the
    `typed_property_retrieval` subgraph -- the same capability exposed as a discoverable, cross-agent MCP tool
    (`retrieve_typed_property_spans`, served by `rag_wright.mcp.typed_property_retrieval_server`) so an agent can
    call it as ONE tool via ARD search instead of embedding the subgraph. Distinct ARD identity from the
    in-process `typed_property_retrieval` subgraph; same output contract `TypedPropertyRetrieval`."""
    registry.register(
        "typed_property_retrieval_mcp",
        contract=TypedPropertyRetrieval,
        kind="mcp_tool",
        display_name="Typed property-boosted retrieval (MCP tool)",
    )


def main() -> None:
    """Serve the typed_property_retrieval MCP tool over stdio. `RAG_MCP_DEMO=1` uses the no-infra demo retriever."""
    retrieval_fn = demo_retrieval_fn() if os.environ.get("RAG_MCP_DEMO") == "1" else production_retrieval_fn()
    build_typed_property_retrieval_mcp(retrieval_fn).run(transport="stdio")


if __name__ == "__main__":
    main()
