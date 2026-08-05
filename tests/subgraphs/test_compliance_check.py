"""CC-6 (compliance §13.3): the `compliance_check` subgraph -- the headline composite.

extract_claims (CC-3) -> retrieve_applicable_requirements (claim scope <-> requirement applicability, with a
section->claim_type map for empty-scope requirements) -> judge per (claim, requirement) pair (CC-4, concurrent,
with AD-LEVEL disclosure context) -> assemble (cited findings + gap matrix + summary). Query-side posture:
degrade-to-empty on failure, never crash; conservative default. Hermetic: all seams stubbed, no LLM/DB.
"""

from __future__ import annotations

from rag_wright.capabilities.compliance_judgment import JudgeVerdict
from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.contracts.compliance import (
    Claim,
    ClaimType,
    ComplianceReport,
    Constraint,
    DeonticType,
    Requirement,
    Verdict,
)
from rag_wright.subgraphs.compliance_check import (
    applies_to,
    build_compliance_check,
    register_compliance_check,
)


def _claim(text="clinically proven to work", ctype=ClaimType.HEALTH, disc=None) -> Claim:
    return Claim(claim_id=Claim.make_id("ad", 0, text), source_doc="ad", claim_type=ctype,
                 assertion_text=text, disclosures_present=disc or [])


def _req(section="255.5", scope=("endorsement",), text="A material connection must be disclosed.") -> Requirement:
    return Requirement(requirement_id=Requirement.make_id("FTC 16 CFR 255", section, text),
                       source="FTC 16 CFR 255", citation=f"§ {section}", deontic_type=DeonticType.OBLIGATION,
                       actor="advertiser",
                       applicability_scope=[Constraint(dimension="claim_type", value=v) for v in scope],
                       requirement_text=text)


# --- applicability: section-based (context), extracted scope only broadens (the ontology enrichment) ---------


def test_context_scoped_guides_apply_broadly():
    # the FTC endorsement guides apply by CONTEXT, not claim_type: an efficacy/health claim in an endorsement
    # IS subject to §255.5 disclosure, so §255.5 applies to any claim type (context-based)
    r = _req(scope=(), text="Disclose material connections.")
    assert applies_to(r, _claim(ctype=ClaimType.HEALTH)) is True
    assert applies_to(r, _claim(ctype=ClaimType.ENDORSEMENT)) is True
    assert applies_to(_req(section="255.1", scope=()), _claim(ctype=ClaimType.PRICING)) is True


def test_definitions_section_applies_to_nothing():
    # §255.0 (Purpose and definitions) is not an operative rule -> excluded (empty section set + no scope)
    assert applies_to(_req(section="255.0", scope=()), _claim(ctype=ClaimType.HEALTH)) is False


def test_extracted_scope_only_broadens_never_excludes():
    # an unmapped section defaults to all; extracted scope can ADD a type but a noisy scope must not EXCLUDE a
    # claim the section covers (§255.5 with a granite 'endorsement' scope still applies to an efficacy claim)
    assert applies_to(_req(section="255.5", scope=("endorsement",)), _claim(ctype=ClaimType.EFFICACY)) is True


# --- the composite: extract -> retrieve -> judge -> assemble -------------------------------------


def _graph(claims, requirements, judge_fn):
    return build_compliance_check(
        claims_fn=lambda text, source: claims,
        requirements_fn=lambda: requirements,
        judge_fn=judge_fn,
    )


def test_produces_cited_findings_and_a_summary():
    claims = [_claim(ctype=ClaimType.ENDORSEMENT)]
    reqs = [_req(scope=("endorsement",))]
    graph = _graph(claims, reqs, lambda c, r: JudgeVerdict(verdict="violation", rationale="no disclosure", confidence=0.9))
    report = graph.invoke({"subject_text": "…ad…", "source_doc": "ad"})["report"]
    assert isinstance(report, ComplianceReport)
    assert len(report.findings) == 1 and report.findings[0].verdict is Verdict.VIOLATION
    assert report.summary == {"violation": 1}
    assert report.gap_matrix and report.gap_matrix[0]["citation"] == "§ 255.5"


def test_only_applicable_pairs_are_judged():
    # a §255.0 (definitions) requirement applies to no claim -> no applicable pair -> no findings
    graph = _graph([_claim(ctype=ClaimType.PRICING)], [_req(section="255.0", scope=(), text="Endorsement means…")],
                   lambda c, r: JudgeVerdict(verdict="violation", rationale="", confidence=1.0))
    report = graph.invoke({"subject_text": "x", "source_doc": "ad"})["report"]
    assert report.findings == []


def test_ad_level_disclosures_reach_the_judge():
    # one claim discloses #ad, another has none; both must be judged WITH the ad-level disclosure set
    seen = {}

    def judge(claim, req):
        seen[claim.assertion_text] = set(claim.disclosures_present)
        return JudgeVerdict(verdict="compliant", rationale="", confidence=0.9)

    disclosed = _claim(text="two shades whiter", ctype=ClaimType.ENDORSEMENT, disc=["#ad"])
    bare = _claim(text="available now", ctype=ClaimType.ENDORSEMENT, disc=[])
    graph = _graph([disclosed, bare], [_req(scope=("endorsement",))], judge)
    graph.invoke({"subject_text": "x", "source_doc": "ad"})
    assert "#ad" in seen["available now"]  # the bare fragment sees the ad-level #ad (the CC-4 residual fix)


def test_degrades_to_empty_when_extraction_fails():
    def boom(text, source):
        raise RuntimeError("extractor down")

    graph = build_compliance_check(claims_fn=boom, requirements_fn=lambda: [_req()],
                                   judge_fn=lambda c, r: JudgeVerdict(verdict="violation", rationale="", confidence=1.0))
    report = graph.invoke({"subject_text": "x", "source_doc": "ad"})["report"]
    assert report.findings == [] and report.source_doc == "ad"  # never crashes; empty report


def test_registers_as_a_subgraph():
    reg = CapabilityRegistry()
    register_compliance_check(reg)
    entry = reg.get("compliance_check")
    assert entry.kind == "subgraph" and entry.contract is ComplianceReport
