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
from typing import Any, Callable

from fastmcp import FastMCP

from rag_wright.contracts.compliance import Claim, ComplianceFinding, ComplianceReport, Verdict

# check_fn: (ad_text, source_doc) -> ComplianceReport. Injected so the server is testable without infra.
CheckFn = Callable[[str, str], ComplianceReport]

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


def build_compliance_mcp(check_fn: CheckFn, *, name: str = "rag-wright-compliance") -> FastMCP:
    """Build the FastMCP server exposing `compliance_check` as one tool. `check_fn` is injected (real subgraph
    in production; a stub in tests) so the MCP surface is testable with no ArcadeDB / LLM."""
    mcp: FastMCP = FastMCP(
        name=name,
        instructions=(
            "Advertising-compliance tools over the FTC 16 CFR 255 endorsement-rule knowledge graph. "
            "Use check_ad_compliance to screen an ad's claims for endorsement/testimonial violations."
        ),
    )

    @mcp.tool(name="check_ad_compliance", description=_TOOL_DESCRIPTION)
    def check_ad_compliance(ad_text: str, source_doc: str = "ad") -> dict[str, Any]:
        """Screen one advertisement for FTC endorsement-rule compliance.

        Args:
            ad_text: The full advertisement copy to screen.
            source_doc: A short identifier for the ad (used in citations). Defaults to "ad".

        Returns:
            A cited compliance report: {verdict, source_doc, summary, findings[], gap_matrix[]}.
        """
        return _report_to_dict(check_fn(ad_text, source_doc))

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

    def _check(ad_text: str, source_doc: str) -> ComplianceReport:
        return run_compliance_check(
            ad_text, source_doc, store=store, extract_model=extract_model,
            judge_model_id=judge_model_id, embedder=embedder, k=k)

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

    def _check(ad_text: str, source_doc: str) -> ComplianceReport:
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


def main() -> None:
    """Serve the compliance MCP tool over stdio. `RAG_MCP_DEMO=1` uses the no-infra demo checker."""
    check_fn = demo_check_fn() if os.environ.get("RAG_MCP_DEMO") == "1" else production_check_fn()
    build_compliance_mcp(check_fn).run(transport="stdio")


if __name__ == "__main__":
    main()
