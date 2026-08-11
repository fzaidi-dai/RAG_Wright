"""CC-6 (compliance §13.3): the `compliance_check` subgraph -- the headline composite of the compliance module.

A hardened, query-side LangGraph subgraph on `scaffold.py`: for a subject ad, extract its claims (CC-3),
retrieve the APPLICABLE requirements (claim scope <-> requirement applicability), judge each `(claim,
requirement)` pair (CC-4, concurrent), and assemble cited findings + a per-requirement gap matrix + a verdict
summary. Two design requirements from earlier findings are built in here:

- **Section->claim_type applicability map** (the ontology enrichment): a requirement whose extracted
  `applicability_scope` is empty is applied by its FTC section (255.5 material-connections -> endorsement, 255.1
  general -> all, 255.0 definitions -> none). Authored in code referencing `ClaimType` (typos are test failures,
  no drifting .ttl) -- the JUDGE-ONTOLOGY-1 pattern. See [[ontology-lever-vs-extraction-lever]].
- **Ad-level disclosure aggregation** (the CC-4 residual fix): disclosures are ad-level but claims are per-span,
  so before judging, each claim's disclosures are enriched with the union across the whole ad -- a per-span
  fragment with disc=none is not over-flagged when the ad as a whole discloses.

Query-side posture: every node degrades to empty on failure (never crash); the judge's conservative default
(needs_review) plus per-finding human-gating carry the trust guarantees.
"""

from __future__ import annotations

from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from rag_wright.capabilities.compliance_judgment import JudgeFn, judge_pairs
from rag_wright.capabilities.retrieval_core import _cosine
from rag_wright.contracts.compliance import (
    CheckableFact,
    Claim,
    ClaimType,
    ComplianceFinding,
    ComplianceReport,
    Requirement,
    RuleScope,
    Verdict,
)
from rag_wright.subgraphs.scaffold import DEFAULT_RETRY, business_span

_ALL_CLAIM_TYPES = {c.value for c in ClaimType}

# The authored section -> applicable claim_types map (the ontology enrichment; backfills empty-scope requirements).
# KEY FINDING (CC-6 live smoke): the FTC endorsement guides apply by CONTEXT (is the ad an endorsement?), NOT by
# claim_type -- an efficacy/health claim inside an influencer post IS subject to §255.5 disclosure. So claim_type
# is the wrong narrowing axis for THIS corpus: every operative section applies broadly; only the definitions
# section (255.0) is excluded. The map still narrows for a genuinely type-scoped corpus (its real value); here it
# mostly just drops definitions. The real per-claim narrowing (which specific rule is most relevant) is SEMANTIC
# retrieval (Leg-B), the CC-7 refinement. See [[ontology-lever-vs-extraction-lever]].
SECTION_CLAIM_TYPES: dict[str, set[str]] = {
    "255.0": set(),  # Purpose and definitions -- not an operative rule; applies to nothing
    "255.1": _ALL_CLAIM_TYPES,  # General considerations
    "255.2": _ALL_CLAIM_TYPES,  # Consumer endorsements -- any claim made via a testimonial
    "255.3": _ALL_CLAIM_TYPES,  # Expert endorsements -- any claim made by/through an expert
    "255.4": _ALL_CLAIM_TYPES,  # Endorsements by organizations
    "255.5": _ALL_CLAIM_TYPES,  # Disclosure of material connections -- any claim in an endorsement needs disclosure
    "255.6": _ALL_CLAIM_TYPES,  # Endorsements directed to children
}


def _section_of(citation: str) -> str:
    """'§ 255.5' -> '255.5' (the section key for the applicability map)."""
    return (citation or "").replace("§", "").strip()


def applicable_claim_types(requirement: Requirement) -> set[str]:
    """The claim types a requirement applies to: the section's applicable set (context-based, the authoritative
    axis for these context-scoped guides) BROADENED by any extracted claim_type scope. Extracted scope can only
    ADD (granite's per-rule claim_type is noisy and must not wrongly EXCLUDE a claim the section covers); an
    unknown section defaults to all (recall-first). So for the FTC guides every operative section applies broadly
    and only definitions (255.0, empty set + no scope) are excluded -- the honest applicability for this corpus.
    The real per-claim narrowing (most-relevant rule) is semantic retrieval (Leg-B), the CC-7 refinement."""
    section = SECTION_CLAIM_TYPES.get(_section_of(requirement.citation), _ALL_CLAIM_TYPES)
    scope = {c.value for c in requirement.applicability_scope if c.dimension == "claim_type"}
    return section | scope


def applies_to(requirement: Requirement, claim: Claim) -> bool:
    """Does `requirement` apply to `claim`? (the claim's type is in the requirement's applicable claim types)."""
    return claim.claim_type.value in applicable_claim_types(requirement)


# CC-8a: the ontology content/context tag (the narrowing routing lever). A CONTEXT section applies regardless of
# claim content (disclosure -> always included); everything else is CONTENT (narrowed by semantic similarity).
SECTION_RULE_SCOPE: dict[str, RuleScope] = {
    "255.5": RuleScope.CONTEXT,  # Disclosure of material connections -- applies to any claim in an endorsement
    "255.4": RuleScope.CONTEXT,  # Endorsements by organizations -- the disclosure/relationship angle
}


def rule_scope_of(requirement: Requirement) -> RuleScope:
    """CONTENT (narrow by similarity) unless the section is a CONTEXT section (disclosure; always-include)."""
    return SECTION_RULE_SCOPE.get(_section_of(requirement.citation), RuleScope.CONTENT)


SelectFn = Callable[[Claim, list], list]  # (claim, requirements) -> the narrowed requirements to judge


def _dedup(requirements: list, vectors: dict, threshold: float) -> list:
    """Greedy near-duplicate collapse: keep a requirement unless it is >= `threshold` cosine-similar to one
    already kept (the 3 near-identical §255.5 disclosure rules -> one). Order-preserving."""
    kept: list = []
    for req in requirements:
        vec = vectors.get(req.requirement_id)
        if vec is not None and any(_cosine(vec, vectors[k.requirement_id]) >= threshold for k in kept
                                   if vectors.get(k.requirement_id) is not None):
            continue
        kept.append(req)
    return kept


def build_select_fn(
    embedder, requirements: list, *, k: int = 5, context_k: int = 3, dedup_threshold: float = 0.92,
    filter_applicability: bool = True
) -> SelectFn:
    """CC-8b: build the semantic-narrowing selector. Precomputes each requirement's BGE vector ONCE. Per claim it
    returns the top-`context_k` CONTEXT rules (disclosure -- kept regardless of content so similarity can't miss
    them, Example B) + the top-`k` CONTENT rules (substantiation etc., ranked by cosine to the claim), deduped.
    Both classes are CAPPED so an over-extracted section (§255.5 -> 32 near-identical disclosure rules) collapses
    to a few representatives rather than re-exploding the cross-product. A precision/cost win that keeps the
    context rules (the recall guarantee) while cutting the redundant-rule noise.

    COMP-VERDICT-GENERIC: `filter_applicability=False` skips the `applies_to` (claim_type) pre-filter -- for a
    generic `CheckableFact` (any domain, no claim_type), narrowing is PURELY SEMANTIC (BGE cosine), so a verdict
    works with no domain applicability ontology. The advertising path keeps `True` (structured claim_type routing)."""
    vectors = {r.requirement_id: embedder.encode_dense(r.requirement_text) for r in requirements}

    def _ranked(reqs: list, claim_vec: list) -> list:
        return sorted(reqs, key=lambda r: _cosine(claim_vec, vectors.get(r.requirement_id, [])), reverse=True)

    def select(claim: Any, reqs: list) -> list:
        applicable = [r for r in reqs if applies_to(r, claim)] if filter_applicability else list(reqs)
        claim_vec = embedder.encode_dense(claim.assertion_text)
        context = _dedup(_ranked([r for r in applicable if rule_scope_of(r) is RuleScope.CONTEXT], claim_vec),
                         vectors, dedup_threshold)[:context_k]
        content = _dedup(_ranked([r for r in applicable if rule_scope_of(r) is RuleScope.CONTENT], claim_vec),
                         vectors, dedup_threshold)[:k]
        return _dedup(context + content, vectors, dedup_threshold)

    return select


ClaimsFn = Callable[[str, str], list]  # (subject_text, source_doc) -> list[Claim]
RequirementsFn = Callable[[], list]  # () -> list[Requirement]


class CheckState(TypedDict, total=False):
    subject_text: str
    source_doc: str
    claims: list
    ad_disclosures: list
    pairs: list
    findings: list
    report: ComplianceReport


def _enrich(claim: Claim, ad_disclosures: set[str]) -> Claim:
    """Enrich a claim's disclosures with the ad-level union (the CC-4 fix). claim_id is content-hashed on the
    assertion, not the disclosures, so it is unchanged -- the finding still cites the original claim."""
    if not ad_disclosures:
        return claim
    merged = sorted(set(claim.disclosures_present) | ad_disclosures)
    return claim.model_copy(update={"disclosures_present": merged})


def build_compliance_check(
    *, claims_fn: ClaimsFn, requirements_fn: RequirementsFn, judge_fn: JudgeFn,
    select_fn: SelectFn | None = None, retry_policy: Any = DEFAULT_RETRY
):
    """Compile the compliance-check subgraph. All seams are injected for hermetic testing. `select_fn` (CC-8b) is
    the per-claim requirement narrower; when None, every APPLICABLE requirement is judged (the broad default).
    Query-side: each node degrades to empty on failure (never crashes) -- an empty report is a safe answer."""

    def extract_claims(state: CheckState) -> CheckState:
        with business_span("compliance_check.extract_claims"):
            try:
                claims = claims_fn(state["subject_text"], state["source_doc"])
            except Exception:  # noqa: BLE001 - degrade-to-empty (query-side never crashes)
                return {"claims": [], "ad_disclosures": []}
        # ad-level disclosure union (CC-4 fix). getattr-tolerant: a generic CheckableFact has no disclosures ->
        # empty union -> _enrich is a no-op, so the same pipeline serves both advertising Claims and bare facts.
        ad = sorted({d for c in claims for d in getattr(c, "disclosures_present", [])})
        return {"claims": claims, "ad_disclosures": ad}

    def retrieve_applicable(state: CheckState) -> CheckState:
        claims = state.get("claims", [])
        ad = set(state.get("ad_disclosures", []))
        with business_span("compliance_check.retrieve_applicable"):
            try:
                requirements = requirements_fn()
            except Exception:  # noqa: BLE001 - degrade-to-empty
                return {"pairs": []}
        if select_fn is not None:  # CC-8b: semantic narrowing (top-k content + always-include context + dedup)
            pairs = [(_enrich(claim, ad), req) for claim in claims for req in select_fn(claim, requirements)]
        else:  # broad default: every applicable requirement
            pairs = [(_enrich(claim, ad), req)
                     for claim in claims for req in requirements if applies_to(req, claim)]
        return {"pairs": pairs}

    def judge(state: CheckState) -> CheckState:
        pairs = state.get("pairs", [])
        if not pairs:
            return {"findings": []}
        with business_span("compliance_check.judge"):
            return {"findings": judge_pairs(pairs, judge_fn=judge_fn)}  # concurrent; conservative default inside

    def assemble(state: CheckState) -> CheckState:
        findings: list[ComplianceFinding] = state.get("findings", [])
        summary: dict[str, int] = {}
        for f in findings:
            summary[f.verdict.value] = summary.get(f.verdict.value, 0) + 1
        # gap matrix: one row per requirement that was checked, rolled up to its worst verdict
        rank = {Verdict.VIOLATION: 3, Verdict.NEEDS_REVIEW: 2, Verdict.COMPLIANT: 1}
        by_req: dict[str, dict] = {}
        for f in findings:
            row = by_req.setdefault(f.requirement_id, {
                "requirement_id": f.requirement_id, "citation": f.citation_requirement.split(" (")[0],
                "verdict": f.verdict, "claims_checked": 0})
            row["claims_checked"] += 1
            if rank[f.verdict] > rank[row["verdict"]]:
                row["verdict"] = f.verdict
        gap_matrix = [{**r, "verdict": r["verdict"].value} for r in by_req.values()]
        report = ComplianceReport(
            source_doc=state["source_doc"], findings=findings, summary=summary, gap_matrix=gap_matrix)
        return {"report": report}

    g = StateGraph(CheckState)
    g.add_node("extract_claims", extract_claims, retry_policy=retry_policy)
    g.add_node("retrieve_applicable", retrieve_applicable, retry_policy=retry_policy)
    g.add_node("judge", judge, retry_policy=retry_policy)
    g.add_node("assemble", assemble)
    g.add_edge(START, "extract_claims")
    g.add_edge("extract_claims", "retrieve_applicable")
    g.add_edge("retrieve_applicable", "judge")
    g.add_edge("judge", "assemble")
    g.add_edge("assemble", END)
    return g.compile()


def _requirement_from_row(row: dict) -> Requirement:
    """Reconstruct a `Requirement` from a stored row (all_requirements), parsing applicability_json back to
    constraints. Lenient: a bad row would raise, but the store wrote validated contracts."""
    import json

    from rag_wright.contracts.compliance import Constraint, DeonticType, Severity
    from rag_wright.contracts.provenance import ConfidenceTag

    scope = [Constraint(dimension=d, value=v) for d, v in json.loads(row.get("applicability_json") or "[]")]
    sev = row.get("severity") or None
    return Requirement(
        requirement_id=row["requirement_id"], source=row["source"], citation=row["citation"],
        deontic_type=DeonticType(row["deontic_type"]), actor=row["actor"],
        applicability_scope=scope, requirement_text=row["requirement_text"],
        evidence_standard=row.get("evidence_standard") or None,
        severity=Severity(sev) if sev else None,
        confidence=ConfidenceTag(row.get("confidence") or "EXTRACTED"))


def production_compliance_check(
    store: Any, *, extract_model: Any, judge_model_id: str, embedder: Any = None, k: int = 5
):
    """Wire the real capabilities: claims = claim_extraction (CC-3), requirements = the store's Requirement KG
    (CC-5), judge = the Granite compliance judge (CC-4). Requirements are loaded ONCE here; when an `embedder` is
    given, CC-8b semantic narrowing is enabled (top-k content + always-include context + dedup), else broad."""
    from rag_wright.capabilities.claim_extraction import claim_extraction
    from rag_wright.capabilities.compliance_judgment import build_compliance_judge_fn

    requirements = [_requirement_from_row(r) for r in store.all_requirements()]
    select_fn = build_select_fn(embedder, requirements, k=k) if embedder is not None else None
    return build_compliance_check(
        claims_fn=lambda text, source: claim_extraction(text, model=extract_model, source_doc=source),
        requirements_fn=lambda: requirements,
        judge_fn=build_compliance_judge_fn(judge_model_id),
        select_fn=select_fn,
    )


def run_compliance_check(
    subject_text: str, source_doc: str, *, store: Any, extract_model: Any, judge_model_id: str,
    embedder: Any = None, k: int = 5,
) -> ComplianceReport:
    """Run a compliance check for one subject document against the Requirement KG -> a cited `ComplianceReport`."""
    graph = production_compliance_check(
        store, extract_model=extract_model, judge_model_id=judge_model_id, embedder=embedder, k=k)
    return graph.invoke({"subject_text": subject_text, "source_doc": source_doc})["report"]


def generic_facts_fn(subject_text: str, source_doc: str) -> list:
    """COMP-VERDICT-GENERIC: the domain-agnostic subject producer -- the subject as ONE `CheckableFact` (no
    advertising claim structure). MVP granularity: the whole subject is one fact judged against the semantically
    relevant requirements. (Sentence/paragraph segmentation for finer citations is a later refinement.)"""
    text = (subject_text or "").strip()
    if not text:
        return []
    return [CheckableFact(fact_id=CheckableFact.make_id(source_doc, 0, text), source_doc=source_doc,
                          assertion_text=text)]


def production_generic_compliance_check(store: Any, *, judge_model_id: str, embedder: Any, k: int = 8):
    """COMP-VERDICT-GENERIC: wire the DOMAIN-AGNOSTIC verdict path -- generic subject facts (no claim_type),
    SEMANTIC-ONLY requirement narrowing (`filter_applicability=False`, no domain applicability ontology needed),
    and the GENERIC judge (text-only). Gives a cited LLM verdict in ANY compliance domain; enrichment
    (COMP-APPLIC-1) only ADDS structured precision on top. `embedder` is required (semantic retrieval is the
    narrowing here)."""
    from rag_wright.capabilities.compliance_judgment import build_generic_judge_fn

    requirements = [_requirement_from_row(r) for r in store.all_requirements()]
    select_fn = build_select_fn(embedder, requirements, k=k, filter_applicability=False)
    return build_compliance_check(
        claims_fn=generic_facts_fn,
        requirements_fn=lambda: requirements,
        judge_fn=build_generic_judge_fn(judge_model_id),
        select_fn=select_fn,
    )


def run_generic_compliance_verdict(
    subject_text: str, source_doc: str, *, store: Any, judge_model_id: str, embedder: Any, k: int = 8,
) -> ComplianceReport:
    """COMP-VERDICT-GENERIC: a domain-agnostic compliance verdict for a free-text subject against the Requirement
    KG -- semantic-retrieve the relevant requirements -> LLM-judge -> cited `ComplianceReport`. Works with NO
    domain applicability enrichment (the always-answer guarantee); suggest COMP-APPLIC-1 for structured precision."""
    graph = production_generic_compliance_check(store, judge_model_id=judge_model_id, embedder=embedder, k=k)
    return graph.invoke({"subject_text": subject_text, "source_doc": source_doc})["report"]


def register_compliance_check(registry) -> None:
    """Register `compliance_check` (subgraph; CC-6). Contract = `ComplianceReport`."""
    registry.register(
        "compliance_check",
        contract=ComplianceReport,
        kind="subgraph",
        display_name="Compliance check (subject doc x requirements -> cited findings + gap matrix)",
    )
