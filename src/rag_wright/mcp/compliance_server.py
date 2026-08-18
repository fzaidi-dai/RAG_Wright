"""MCP-PROTO: the reference `compliance_check` MCP-tool server (FastMCP).

Wraps the `compliance_check` SUBGRAPH (CC-6) as a single MCP tool `check_ad_compliance(ad_text, source_doc)`
-> a cited `ComplianceReport` (as JSON). The store (the FTC 16 CFR 255 Requirement KG) + the models (Granite
judge/extraction via the seam, BGE narrowing via the A100 adapter) bind SERVER-SIDE from env, so the tool call
is just `{ad_text}` -- the token/coordination win. An external agent discovers this via ARD search and calls it.

Grounded (library rule): FastMCP `server.py:L278` (`FastMCP(name, instructions, version=...)`), `@mcp.tool`,
`run(transport=...)` -- confirmed against the cloned-repo AST graph (`graphify-out/fastmcp/graph.json`) + the
installed 3.4.6 signatures.

`build_compliance_mcp(check_fn)` injects the checker so the server is hermetically testable (a stub `check_fn`,
no ArcadeDB/LLM). `main()` picks the production checker (real subgraph, env-wired) or a deterministic demo
checker (`RAG_MCP_DEMO=1`, no infra) and serves over stdio (so a Deep Agent can spawn it).

  # real (needs the compliance KG + a model backend via env, like scripts/eval_compliance_gold.py):
  uv run --no-sync python -m rag_wright.mcp.compliance_server
  # demo (no infra -- deterministic report; for the Deep-Agent prototype):
  RAG_MCP_DEMO=1 uv run --no-sync python -m rag_wright.mcp.compliance_server
"""

from __future__ import annotations

import os
from typing import Any, Awaitable, Callable

from fastmcp import FastMCP

from rag_wright.contracts.compliance import Claim, ComplianceFinding, ComplianceReport, Verdict

# check_fn: (ad_text, source_doc) -> ComplianceReport. Injected so the server is testable without infra.
# ASYNC-C1 (ADR-0057): async -- the tool handler awaits it, and it awaits the async compliance_check subgraph.
CheckFn = Callable[[str, str], Awaitable[ComplianceReport]]

_TOOL_DESCRIPTION = (
    "Check an advertisement's claims against the FTC endorsement & testimonial rules (16 CFR Part 255). "
    "Extracts the ad's objective claims, matches each to the applicable regulatory requirements, and judges "
    "them from the AD TEXT ALONE, returning a cited compliance report: an ad-level verdict (violation / "
    "needs_review / compliant), per-claim findings each with a rationale and both-sided citation (the claim "
    "span and the regulation clause), a verdict summary, and a per-requirement gap matrix. A claim that cannot "
    "be verified from the text is escalated to needs_review (human-gated), never silently cleared or flagged."
)


def _report_to_dict(report: ComplianceReport) -> dict[str, Any]:
    """The tool's JSON payload: the report plus its computed ad-level `verdict` (a @property, so not in
    model_dump). This is the structured content the calling agent receives."""
    return {"verdict": report.verdict.value, **report.model_dump(mode="json")}


_GENERIC_TOOL_DESCRIPTION = (
    "Check ANY subject (a practice, document, or scenario) against the ingested regulation knowledge graph and "
    "return a cited LLM compliance verdict -- WITHOUT needing a domain-specific applicability ontology. It "
    "semantically retrieves the most relevant requirements and judges the subject against each from the text, "
    "returning a cited report (verdict / needs_review / compliant, per-requirement findings with both-sided "
    "citations, summary, gap matrix) plus a note suggesting domain applicability enrichment for more precise "
    "routing. Use this for any regulatory domain; use check_ad_compliance for the advertising-tuned path."
)


def build_compliance_mcp(check_fn: CheckFn, *, name: str = "rag-wright-compliance",
                         generic_check_fn: CheckFn | None = None) -> FastMCP:
    """Build the FastMCP server exposing the compliance tools. `check_fn` = the advertising `check_ad_compliance`
    (injected: real subgraph in production, a stub in tests). `generic_check_fn` (optional) adds the
    domain-agnostic `check_compliance` tool (COMP-VERDICT-GENERIC). Both testable with no ArcadeDB / LLM."""
    mcp: FastMCP = FastMCP(
        name=name,
        instructions=(
            "Compliance tools over a deontic Requirement knowledge graph. `check_ad_compliance` is the "
            "advertising-tuned path (FTC 16 CFR 255, structured claim-type routing). `check_compliance` is the "
            "DOMAIN-AGNOSTIC path: it gives a cited LLM verdict for ANY subject against ANY ingested regulation, "
            "even one whose domain has no applicability ontology yet."
        ),
    )

    @mcp.tool(name="check_ad_compliance", description=_TOOL_DESCRIPTION)
    async def check_ad_compliance(ad_text: str, source_doc: str = "ad") -> dict[str, Any]:
        """Screen one advertisement for FTC endorsement-rule compliance.

        Args:
            ad_text: The full advertisement copy to screen.
            source_doc: A short identifier for the ad (used in citations). Defaults to "ad".

        Returns:
            A cited compliance report: {verdict, source_doc, summary, findings[], gap_matrix[]}.
        """
        return _report_to_dict(await check_fn(ad_text, source_doc))

    if generic_check_fn is not None:  # COMP-VERDICT-GENERIC: the domain-agnostic verdict tool (any domain)
        @mcp.tool(name="check_compliance", description=_GENERIC_TOOL_DESCRIPTION)
        async def check_compliance(subject_text: str, source_doc: str = "subject") -> dict[str, Any]:
            """Check any subject against the ingested regulation KG -> a cited LLM verdict, WITHOUT needing a
            domain-specific applicability ontology (semantic-retrieve relevant requirements -> LLM-judge).

            Args:
                subject_text: The practice / document / scenario to check for compliance.
                source_doc: A short identifier for the subject (used in citations). Defaults to "subject".

            Returns:
                A cited compliance report {verdict, source_doc, summary, findings[], gap_matrix[]}, plus a
                `note` suggesting domain applicability enrichment for more precise claim<->requirement routing.
            """
            out = _report_to_dict(await generic_check_fn(subject_text, source_doc))
            out["note"] = ("Generic domain-agnostic verdict (semantic retrieval + LLM judge). For more precise "
                           "claim<->requirement routing in this domain, enrich its applicability dimensions.")
            return out

    return mcp


# --- production checker: the real compliance_check subgraph, env-wired (like scripts/eval_compliance_gold.py) -


def production_check_fn(*, k: int = 5) -> CheckFn:
    """Wire the real `compliance_check` over the env-selected store + models: ArcadeDB compliance KG
    (`ARCADEDB_*`), Granite judge/extraction (`RAG_SERVING`), BGE narrowing (`STACK_URL` -> A100, else local).
    Heavy imports are lazy so `RAG_MCP_DEMO` never pays for them."""
    from dotenv import load_dotenv

    load_dotenv()
    from rag_wright.capabilities.dg_extraction import default_extraction_model
    from rag_wright.capabilities.remote_encoders import query_embedder
    from rag_wright.models.profiles import ModelRole, model_for
    from rag_wright.store.arcadedb import ArcadeDBStore
    from rag_wright.subgraphs.compliance_check import run_compliance_check

    store = ArcadeDBStore.from_env(database=os.environ.get("COMPLIANCE_DB", "ragwright_compliance"))
    extract_model = default_extraction_model("claim-extract", "ibm-granite/granite-4.1-8b")
    judge_model_id = model_for(ModelRole.STRUCTURED_REASONING)
    embedder = query_embedder()

    async def _check(ad_text: str, source_doc: str) -> ComplianceReport:
        return await run_compliance_check(
            ad_text, source_doc, store=store, extract_model=extract_model,
            judge_model_id=judge_model_id, embedder=embedder, k=k)

    return _check


def production_generic_check_fn(*, k: int = 8) -> CheckFn:
    """COMP-VERDICT-GENERIC: wire the DOMAIN-AGNOSTIC verdict over the env-selected store + models (no claim
    extraction; semantic-retrieve + generic judge). Same infra as `production_check_fn`."""
    from dotenv import load_dotenv

    load_dotenv()
    from rag_wright.capabilities.remote_encoders import query_embedder
    from rag_wright.models.profiles import ModelRole, model_for
    from rag_wright.store.arcadedb import ArcadeDBStore
    from rag_wright.subgraphs.compliance_check import run_generic_compliance_verdict

    store = ArcadeDBStore.from_env(database=os.environ.get("COMPLIANCE_DB", "ragwright_compliance"))
    judge_model_id = model_for(ModelRole.STRUCTURED_REASONING)
    embedder = query_embedder()

    async def _check(subject_text: str, source_doc: str) -> ComplianceReport:
        return await run_generic_compliance_verdict(
            subject_text, source_doc, store=store, judge_model_id=judge_model_id, embedder=embedder, k=k)

    return _check


# --- demo checker: a deterministic, real-shaped report (no ArcadeDB / LLM) for the Deep-Agent prototype -------


def demo_check_fn() -> CheckFn:
    """A deterministic stub with the REAL contract shape -- flags two unsubstantiated proof-overclaims (so the
    ad-level rollup is VIOLATION, >= the threshold of 2). Lets the Deep-Agent prototype (and the hermetic test)
    exercise the full MCP path with no ArcadeDB / LLM."""
    _overclaims = [
        ("clinically proven to erase deep wrinkles", "'clinically proven' with no cited study in the ad text"),
        ("guaranteed to reverse aging in 7 days", "'guaranteed' result claim with no substantiation shown"),
    ]

    async def _check(ad_text: str, source_doc: str) -> ComplianceReport:
        findings = [
            ComplianceFinding(
                claim_id=Claim.make_id(source_doc, i, assertion),
                requirement_id="req-255.2-substantiation", verdict=Verdict.VIOLATION,
                rationale=f"Overclaims proof: {why}.",
                citation_claim=f"{source_doc}: {assertion}",
                citation_requirement="§ 255.2 (req-255.2-substantiation): objective claims must be substantiated",
                confidence=0.95)
            for i, (assertion, why) in enumerate(_overclaims)
        ]
        return ComplianceReport(
            source_doc=source_doc, findings=findings, summary={"violation": len(findings)},
            gap_matrix=[{"requirement_id": "req-255.2-substantiation", "citation": "§ 255.2",
                         "verdict": "violation", "claims_checked": len(findings)}])

    return _check


def register_compliance_check_mcp(registry) -> None:
    """Register `compliance_check_mcp` (MCP-PROTO): the ARD `mcp_tool` surface of the `compliance_check`
    subgraph -- the same capability exposed as a discoverable, cross-agent MCP tool (`check_ad_compliance`,
    served by `rag_wright.mcp.compliance_server`) so an agent can call it as ONE tool via ARD search instead of
    embedding the subgraph. Distinct ARD identity from the in-process `compliance_check` subgraph; same output
    contract `ComplianceReport`."""
    registry.register(
        "compliance_check_mcp",
        contract=ComplianceReport,
        kind="mcp_tool",
        display_name="Ad compliance check (MCP tool)",
    )


def main() -> None:
    """Serve the compliance MCP tool over stdio. `RAG_MCP_DEMO=1` uses the no-infra demo checker."""
    check_fn = demo_check_fn() if os.environ.get("RAG_MCP_DEMO") == "1" else production_check_fn()
    build_compliance_mcp(check_fn).run(transport="stdio")


if __name__ == "__main__":
    main()
