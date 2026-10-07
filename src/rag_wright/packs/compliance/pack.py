"""The COMPLIANCE pack (built on the contracts pack): its capability slugs and ARD manifests (ING-8c; moved from
`rag_wright.reference.pack`). `register()` registers the contracts pack first, since compliance ingestion and
checking run on the contract extraction stack, then adds this pack's slugs and manifests."""

from __future__ import annotations

from rag_wright.capabilities.ard import ResponseBounds  # noqa: F401 - used by some specs
from rag_wright.capabilities.manifests import (  # noqa: F401 - the spec dataclass + shared helpers the specs use
    _RLM_GRANTED,
    CapabilityManifest,
    engine_capabilities,
    register_capability,
)
from rag_wright.capabilities.registry import register_canonical_slugs
from rag_wright.packs.contracts import pack as contracts_pack

COMPLIANCE_CAPABILITY_SLUGS: frozenset[str] = frozenset({
    "claim_adaptation",
    "claim_extraction",
    "compliance_check",
    "compliance_check_mcp",
    "compliance_finding_assembly",
    "compliance_ingestion",
    "compliance_judgment",
    "requirement_adaptation",
    "requirement_extraction",
})

COMPLIANCE_SPECS: tuple[CapabilityManifest, ...] = (
    CapabilityManifest(
        slug="requirement_extraction",
        kind="subgraph",  # extract(docling-graph, multi-call auto/dense) -> adapt; a workflow, not a single act
        display_name="Requirement extraction (regulatory section -> deontic rules; subgraph)",
        description=(
            "Extract the deontic rules a regulatory section states into typed Requirement nodes as a hardened "
            "LangGraph subgraph (CC-2, compliance §13.1, SKILL-SPLIT): extract (the docling-graph extraction act "
            "using the skills/requirement_extraction/ template, extraction_contract='auto' -> dense/multi-call "
            "on long sections) -> adapt (requirement_adaptation). A subgraph because the extraction is "
            "multi-LLM-call and the extract->adapt chaining is deterministic. A transient extraction failure "
            "retries then dead-letters the section. The regulatory side of the compliance module's ingestion."
        ),
        representative_queries=(
            "extract the rules a regulation section states as typed requirements",
            "turn FTC endorsement-guide text into cited deontic requirement nodes",
            "parse a regulatory corpus into obligation/prohibition/permission rules",
        ),
        tags=("compliance", "extraction", "deontic", "regulatory", "subgraph"),
    ),
    CapabilityManifest(
        slug="requirement_adaptation",
        kind="function",
        display_name="Requirement adaptation (extracted section -> validated Requirements)",
        description=(
            "DETERMINISTIC adaptation (CC-2, SKILL-SPLIT): map the requirement_extraction subgraph's raw "
            "ExtractedRegulationSection to validated CC-1 Requirement nodes -- deontic force coerced to the "
            "closed vocab (off-vocab -> AMBIGUOUS, kept not dropped), applicability_scope from the claim_types "
            "(off-vocab dropped), citation = the section, content-hash requirement_id. No model."
        ),
        representative_queries=(
            "adapt an extracted regulation section into validated requirements",
            "coerce extracted deontic force to the closed vocab with a conservative fallback",
            "attach citations and ids to extracted regulatory rules",
        ),
        tags=("compliance", "adaptation", "deontic", "deterministic"),
    ),
    CapabilityManifest(
        slug="claim_extraction",
        kind="agent_skill",  # a single docling-graph LLM extraction act, authored as skills/claim_extraction/
        display_name="Claim extraction (subject ad -> checkable claims; authored skill)",
        description=(
            "The subject-document claim-extraction METHOD (CC-3, compliance §13.1), authored as an agent skill "
            "(skills/claim_extraction/: SKILL.md + the template.py schema asset ExtractedAd/ExtractedClaim) and "
            "applied through the docling-graph + model seam (Granite, ADR-0039): read an ad and pull out its "
            "distinct CHECKABLE assertions, each with its kind, the disclosures present near it, and whether the "
            "ad references evidence. One 'direct' call (ads are short). The deterministic mapping to the closed "
            "Claim vocab is the claim_adaptation FUNCTION's job, not the skill's."
        ),
        representative_queries=(
            "extract the checkable claims an ad makes",
            "turn a marketing campaign into claims with disclosures and evidence flags",
            "identify the health/efficacy/endorsement claims in a subject document",
        ),
        tags=("compliance", "extraction", "claims", "advertising", "skill"),
    ),
    CapabilityManifest(
        slug="claim_adaptation",
        kind="function",
        display_name="Claim adaptation (extracted ad -> validated Claims)",
        description=(
            "DETERMINISTIC adaptation (CC-3, SKILL-SPLIT): map the claim_extraction skill's raw ExtractedAd to "
            "validated CC-1 Claim nodes -- claim_type coerced to the closed vocab (an off-vocab value kept but "
            "flagged AMBIGUOUS, a checkable assertion is never dropped), disclosures/evidence/medium carried, "
            "the content-hash claim_id + span provenance attached. No model."
        ),
        representative_queries=(
            "adapt an extracted ad into validated typed claims",
            "coerce extracted claim types to the closed vocab with a conservative fallback",
            "attach span provenance and ids to extracted ad claims",
        ),
        tags=("compliance", "adaptation", "claims", "deterministic"),
    ),
    CapabilityManifest(
        slug="compliance_judgment",
        kind="agent_skill",  # a single grounded LLM judgment act, authored as skills/compliance_judgment/SKILL.md
        display_name="Compliance judgment (claim x requirement -> verdict; authored skill)",
        description=(
            "The advertising-compliance judgment METHOD (CC-4, compliance §13.2), authored as an agent skill "
            "(skills/compliance_judgment/SKILL.md) and applied through the model seam (Granite, ADR-0039): given "
            "one claim + one requirement and ONLY the ad text, decide violation (clearly wrong from the text -- "
            "overclaimed proof without a cited study, missing disclosure, fake review), needs_review (an "
            "objective claim whose substantiation cannot be verified from the text -- escalate), or compliant "
            "(puffery / disclosure present / evidence cited). Reserves violation for clear breaches and escalates "
            "the unverifiable; never a silent pass. The deterministic vocab/citation is the "
            "compliance_finding_assembly FUNCTION's job, not the skill's."
        ),
        representative_queries=(
            "judge whether an ad claim violates a regulatory requirement",
            "decide compliant / violation / needs-review for a claim against a rule",
            "audit a marketing claim against an FTC endorsement requirement",
        ),
        tags=("compliance", "judgment", "verdict", "skill", "human-in-the-loop"),
    ),
    CapabilityManifest(
        slug="compliance_finding_assembly",
        kind="function",
        display_name="Compliance finding assembly (verdict + inputs -> cited finding)",
        description=(
            "DETERMINISTIC assembly (CC-4, SKILL-SPLIT): map the compliance_judgment skill's raw verdict string "
            "to the closed Verdict vocab (an unreadable or missing verdict -> needs_review, the conservative "
            "default), and attach the BOTH-SIDED citation (the exact claim span + the exact requirement clause) "
            "FROM THE INPUTS -- the model never authors a citation -- returning a ComplianceFinding. No model: "
            "the trust guarantees the LLM judgment must not own live here."
        ),
        representative_queries=(
            "assemble a cited compliance finding from a raw judgment verdict",
            "map a verdict string to the closed vocab with a conservative default",
            "attach both-sided citations to a compliance verdict from the inputs",
        ),
        tags=("compliance", "assembly", "deterministic", "citation", "conservative-default"),
    ),
    CapabilityManifest(
        slug="compliance_ingestion",
        kind="subgraph",
        display_name="Compliance ingestion (regulatory corpus -> Requirement KG)",
        description=(
            "Ingest a regulatory corpus into a Requirement KG as a hardened LangGraph subgraph (CC-5, "
            "compliance §13): per section, extract the deontic rules (requirement_extraction) and write them "
            "as Requirement nodes, with a per-section retry -> dead-letter so one bad section never kills the "
            "ingest. Reuses the generic corpus driver (SourceDocument + run_corpus_ingestion) via a thin "
            "RegulationAdapter; the Requirement KG lives in its own database so the contract KG stays clean. "
            "The regulatory-corpus side of the compliance module's ingestion."
        ),
        representative_queries=(
            "ingest a regulation into a requirement knowledge graph",
            "load the FTC endorsement guides as typed deontic requirement nodes",
            "build the requirements KG from a regulatory corpus",
        ),
        tags=("compliance", "ingestion", "regulatory", "subgraph", "langgraph"),
        impl_ref="rag_wright.packs.compliance.subgraphs.compliance_ingestion:ainvoke",  # EP-REF-1c: invocable via ainvoke_subgraph
    ),
    CapabilityManifest(
        slug="compliance_check",
        kind="subgraph",
        display_name="Compliance check (subject doc x requirements -> cited findings + gap matrix)",
        description=(
            "Check a subject advertisement against a regulatory Requirement KG as a hardened LangGraph subgraph "
            "(CC-6, compliance §13.3, the headline composite): extract the ad's claims (claim_extraction) -> "
            "retrieve the applicable requirements (claim scope <-> requirement applicability, with a "
            "section->claim_type map backfilling empty scopes) -> judge each (claim, requirement) pair "
            "(compliance_judgment, concurrent, with ad-level disclosure context) -> assemble cited findings + a "
            "per-requirement gap matrix + a verdict summary. Query-side: degrades to empty on failure; every "
            "violation/needs_review is human-gated. Both-sided cited -- the trust product."
        ),
        representative_queries=(
            "check whether an ad campaign complies with the FTC endorsement guides",
            "produce a cited compliance gap matrix for a marketing document",
            "audit a subject document against a regulatory requirements KG",
        ),
        tags=("compliance", "check", "verdict", "gap-matrix", "subgraph", "langgraph"),
        impl_ref="rag_wright.packs.compliance.subgraphs.compliance_check:ainvoke",  # EP-REF-1c: invocable via ainvoke_subgraph
    ),
    CapabilityManifest(
        slug="compliance_check_mcp",
        kind="mcp_tool",  # the discoverable MCP-tool surface of the compliance_check subgraph (MCP-PROTO)
        display_name="Ad compliance check (MCP tool)",
        description=(
            "The compliance_check capability exposed as a single MCP tool (`check_ad_compliance`, served by "
            "rag_wright.packs.compliance.mcp.compliance_server via FastMCP): screen one advertisement against the FTC 16 CFR 255 "
            "endorsement rules and return a cited ComplianceReport (ad-level verdict, per-claim findings with "
            "both-sided citations, verdict summary, per-requirement gap matrix). An external agent discovers "
            "this via ARD search and calls it as ONE tool -- saving context tokens and inter-agent coordination "
            "vs. embedding the compliance_check subgraph. Same output contract as the subgraph; distinct ARD "
            "identity because the callable surface is a deployed MCP server, not an in-process graph node."
        ),
        representative_queries=(
            "check whether an advertisement complies with the FTC endorsement rules",
            "screen ad copy for unsubstantiated claims and missing disclosures",
            "get a cited compliance report for a marketing claim as a tool call",
        ),
        tags=("compliance", "mcp-tool", "ad-screening", "cited", "discoverable"),
    ),
)


def register() -> None:
    """Register the compliance pack (and the contracts pack it builds on)."""
    contracts_pack.register()
    register_canonical_slugs(COMPLIANCE_CAPABILITY_SLUGS)
    for manifest in COMPLIANCE_SPECS:
        register_capability(manifest)
