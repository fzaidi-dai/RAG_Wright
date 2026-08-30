"""CC-4 (compliance §13.2): `compliance_judgment` -- the one genuinely new capability (the judgment node).

Per (claim, applicable_requirement) -> a verdict {compliant, violation, needs_review} + rationale + BOTH-SIDED
citation + confidence, extending the grounding judge (semantic_judge, ADR-0028/0040) from "is X supported?" to
"does claim X satisfy/violate requirement Y?". Conservative default: uncertainty (or a judge failure, or an
off-vocab verdict) -> needs_review, NEVER a silent compliant/violation. Every violation/needs_review is
human-gated. Hermetic: the judge call is stubbed (`judge_fn`), no LLM.
"""

from __future__ import annotations

from rag_wright.capabilities.compliance_judgment import (
    JudgeVerdict,
    ajudge_pairs,
    assemble_finding,
    build_acompliance_judge_fn,
    build_compliance_judge_fn,
    compliance_judgment,
    judge_pairs,
    register_compliance_finding_assembly,
    register_compliance_judgment,
)
from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.contracts.compliance import (
    CheckableFact,
    Claim,
    ClaimType,
    ComplianceFinding,
    Constraint,
    DeonticType,
    Requirement,
    Verdict,
)


def _claim(**over) -> Claim:
    base = dict(source_doc="influencer_ad", claim_type=ClaimType.HEALTH,
                assertion_text="clinically proven to erase deep wrinkles in 7 days")
    base.update(over)
    base.setdefault("fact_id", Claim.make_id(base["source_doc"], 0, base["assertion_text"]))
    return Claim(**base)


def _req(**over) -> Requirement:
    base = dict(source="FTC 16 CFR 255", citation="§ 255.5", deontic_type=DeonticType.OBLIGATION,
                actor="advertiser", applicability_scope=[Constraint(dimension="claim_type", value="health")],
                requirement_text="A material connection must be disclosed.")
    base.update(over)
    base.setdefault("requirement_id", Requirement.make_id(base["source"], "255.5", base["requirement_text"]))
    return Requirement(**base)


# --- verdict mapping + conservative default ------------------------------------------------------


def test_clear_violation_maps_and_is_human_gated():
    finding = compliance_judgment(
        _claim(), _req(), judge_fn=lambda c, r: JudgeVerdict(verdict="violation", rationale="no disclosure", confidence=0.9))
    assert isinstance(finding, ComplianceFinding)
    assert finding.verdict is Verdict.VIOLATION
    assert finding.needs_human_review is True  # every violation is human-gated
    assert finding.rationale == "no disclosure" and finding.confidence == 0.9


def test_clear_compliant_not_gated():
    finding = compliance_judgment(
        _claim(), _req(), judge_fn=lambda c, r: JudgeVerdict(verdict="compliant", rationale="disclosed", confidence=0.8))
    assert finding.verdict is Verdict.COMPLIANT and finding.needs_human_review is False


def test_judge_failure_defaults_to_needs_review():
    finding = compliance_judgment(_claim(), _req(), judge_fn=lambda c, r: None)
    assert finding.verdict is Verdict.NEEDS_REVIEW and finding.needs_human_review is True
    assert finding.confidence == 0.0


def test_offvocab_verdict_defaults_to_needs_review():
    finding = compliance_judgment(
        _claim(), _req(), judge_fn=lambda c, r: JudgeVerdict(verdict="probably fine", rationale="?", confidence=0.5))
    assert finding.verdict is Verdict.NEEDS_REVIEW  # never silently compliant/violation under an unreadable verdict


# --- both-sided citation comes from the INPUTS, not the LLM (trust) ------------------------------


def test_citations_are_taken_from_the_inputs():
    claim, req = _claim(), _req()
    finding = compliance_judgment(
        claim, req, judge_fn=lambda c, r: JudgeVerdict(verdict="violation", rationale="x", confidence=0.7))
    assert finding.claim_id == claim.fact_id and finding.requirement_id == req.requirement_id
    assert claim.assertion_text in finding.citation_claim
    assert req.citation in finding.citation_requirement  # "§ 255.5"


# --- batch: judge many pairs concurrently --------------------------------------------------------


def test_judge_pairs_returns_one_finding_per_pair():
    pairs = [(_claim(), _req()), (_claim(assertion_text="lose 30 lbs"), _req(citation="§ 255.2"))]
    findings = judge_pairs(pairs, judge_fn=lambda c, r: JudgeVerdict(verdict="needs_review", rationale="", confidence=0.0))
    assert len(findings) == 2 and all(f.verdict is Verdict.NEEDS_REVIEW for f in findings)


# --- ASYNC-C1 (ADR-0057): ajudge_pairs -- concurrent async judging, order preserved, conservative default -----


async def test_ajudge_pairs_one_finding_per_pair_order_preserved():
    pairs = [(_claim(assertion_text="a"), _req()), (_claim(assertion_text="b"), _req(citation="§ 255.2"))]

    async def _judge(claim, req):
        return JudgeVerdict(verdict="violation" if claim.assertion_text == "a" else "compliant",
                            rationale="", confidence=0.9)

    findings = await ajudge_pairs(pairs, ajudge_fn=_judge)
    assert [f.verdict for f in findings] == [Verdict.VIOLATION, Verdict.COMPLIANT]  # order preserved by gather


async def test_ajudge_pairs_timeout_becomes_conservative_needs_review():
    # an async judge that hangs past the deadline -> asyncio.timeout -> a needs_review finding (never a hang)
    import asyncio

    async def slow_judge(claim, req):
        await asyncio.sleep(1.5)
        return JudgeVerdict(verdict="compliant", rationale="", confidence=1.0)

    findings = await ajudge_pairs([(_claim(), _req())], ajudge_fn=slow_judge, timeout_s=0.2, timeout_retries=0)
    assert len(findings) == 1
    assert findings[0].verdict is Verdict.NEEDS_REVIEW and findings[0].needs_human_review is True
    assert findings[0].claim_id and findings[0].requirement_id  # citations preserved from the inputs


async def test_ajudge_pairs_non_timeout_error_propagates():
    # a genuine judge bug is NOT masked as needs_review (matches map_concurrent_async) -- it surfaces
    import pytest

    async def boom(claim, req):
        raise RuntimeError("judge bug")

    with pytest.raises(RuntimeError, match="judge bug"):
        await ajudge_pairs([(_claim(), _req())], ajudge_fn=boom, timeout_s=None)


async def test_build_acompliance_judge_fn_passes_both_sides_through_the_async_seam():
    captured = {}

    class _Model:
        async def ainvoke(self, prompt):
            captured["prompt"] = prompt
            return JudgeVerdict(verdict="violation", rationale="undisclosed", confidence=0.9)

    def fake_factory(model_id, schema):
        assert schema is JudgeVerdict
        return _Model()

    judge = build_acompliance_judge_fn("granite", structured_factory=fake_factory)
    verdict = await judge(_claim(), _req())
    assert verdict.verdict == "violation"
    assert "erase deep wrinkles" in captured["prompt"] and "material connection" in captured["prompt"].lower()


# --- the real judge fn wires the seam + both sides into the prompt -------------------------------


def test_build_judge_fn_passes_both_sides_through_the_seam():
    captured = {}

    class _Model:
        def __init__(self, prompt_sink):
            self._sink = prompt_sink

        def invoke(self, prompt):
            self._sink["prompt"] = prompt
            return JudgeVerdict(verdict="violation", rationale="undisclosed", confidence=0.9)

    def fake_factory(model_id, schema):
        assert schema is JudgeVerdict
        return _Model(captured)

    judge = build_compliance_judge_fn("granite", structured_factory=fake_factory)
    verdict = judge(_claim(), _req())
    assert verdict.verdict == "violation"
    # the prompt must carry BOTH the claim and the requirement (and the disclosure signal)
    assert "erase deep wrinkles" in captured["prompt"] and "material connection" in captured["prompt"].lower()


def test_registers_the_skill_and_the_function_split():
    # SKILL-SPLIT: the LLM judgment is an agent_skill; the deterministic assembly is a function
    reg = CapabilityRegistry()
    register_compliance_judgment(reg)
    register_compliance_finding_assembly(reg)
    skill = reg.get("compliance_judgment")
    assert skill.kind == "agent_skill" and skill.contract is JudgeVerdict
    fn = reg.get("compliance_finding_assembly")
    assert fn.kind == "function" and fn.contract is ComplianceFinding


def test_assemble_finding_is_deterministic_and_conservative():
    # the FUNCTION: no model; None ruling -> conservative needs_review; citations from the inputs
    claim, req = _claim(), _req()
    none_finding = assemble_finding(claim, req, None)
    assert none_finding.verdict is Verdict.NEEDS_REVIEW and none_finding.claim_id == claim.fact_id
    viol = assemble_finding(claim, req, JudgeVerdict(verdict="violation", rationale="x", confidence=0.9))
    assert viol.verdict is Verdict.VIOLATION and req.citation in viol.citation_requirement


def test_checkable_fact_carries_optional_section_locator():
    # UNIFY-A: additive, back-compat -- absent by default, settable to a locator.
    assert CheckableFact(fact_id="f0", source_doc="s", assertion_text="x").section is None
    f = CheckableFact(fact_id="f0", source_doc="s", assertion_text="x", section="4.2")
    assert f.section == "4.2"


def test_assemble_finding_cites_the_section_locator_when_present():
    # UNIFY-A: a section-bearing fact -> the claim-side citation reads "doc § 4.2: <sentence>", so a finding
    # points at the section AND the sentence. The model never authors it (assembled from the input, as before).
    claim = _claim(section="4.2")
    finding = assemble_finding(claim, _req(), JudgeVerdict(verdict="violation", rationale="x", confidence=0.9))
    assert "§ 4.2" in finding.citation_claim
    assert claim.assertion_text in finding.citation_claim and claim.source_doc in finding.citation_claim


def test_checkable_fact_structural_locator_render_helper():
    # SEG-1: locator() renders the structural path -- "§ {section}" + a within-section element marker
    # ("¶N" for a paragraph, "· bullet N" for a list item); empty when there is no section (a paste).
    F = CheckableFact
    base = dict(fact_id="f0", source_doc="s", assertion_text="x")
    assert F(**base).locator() == ""                                              # no structure
    assert F(**base, section="4.2").locator() == "§ 4.2"                          # section only
    assert F(**base, section="4.2", element_kind="paragraph", element_ordinal=3).locator() == "§ 4.2 ¶3"
    assert F(**base, section="4.2", element_kind="list_item", element_ordinal=2).locator() == "§ 4.2 · bullet 2"
    # defaults are None (additive / back-compat)
    f = F(**base)
    assert f.element_kind is None and f.element_ordinal is None


def test_assemble_finding_cites_the_full_structural_locator():
    # SEG-1: the finding's claim-side citation carries the full locator when present.
    para = _claim(section="4.2", element_kind="paragraph", element_ordinal=3)
    f1 = assemble_finding(para, _req(), JudgeVerdict(verdict="violation", rationale="x", confidence=0.9))
    assert "§ 4.2 ¶3" in f1.citation_claim and para.assertion_text in f1.citation_claim

    bullet = _claim(section="4.2", element_kind="list_item", element_ordinal=2)
    f2 = assemble_finding(bullet, _req(), JudgeVerdict(verdict="violation", rationale="x", confidence=0.9))
    assert "§ 4.2 · bullet 2" in f2.citation_claim


def test_assemble_finding_citation_unchanged_when_no_section():
    # UNIFY-A back-compat: no section -> the citation is exactly the old "doc: sentence" (no "§").
    claim = _claim()  # no section
    finding = assemble_finding(claim, _req(), JudgeVerdict(verdict="compliant", rationale="ok", confidence=0.8))
    assert finding.citation_claim == f"{claim.source_doc}: {claim.assertion_text}" and "§" not in finding.citation_claim


def test_skill_method_loads_from_the_skill_md():
    # the SKILL.md is the authored method; it teaches the three verdicts + the ad-text-only constraint
    from rag_wright.capabilities.compliance_judgment import judgment_method
    method = judgment_method()
    assert "needs_review" in method and "only the ad text" in method.lower()
    assert not method.startswith("---")  # frontmatter stripped


def test_judge_pairs_timeout_becomes_conservative_needs_review(monkeypatch):
    # a judge that hangs past the deadline -> map_concurrent times out -> a needs_review finding (never a hang)
    import time
    from rag_wright.contracts.compliance import Verdict

    def slow_judge(claim, req):
        time.sleep(1.5)
        return JudgeVerdict(verdict="compliant", rationale="", confidence=1.0)

    findings = judge_pairs([(_claim(), _req())], judge_fn=slow_judge, timeout_s=0.2)
    assert len(findings) == 1
    assert findings[0].verdict is Verdict.NEEDS_REVIEW and findings[0].needs_human_review is True
    assert findings[0].claim_id and findings[0].requirement_id  # citations preserved from the inputs


# --- COMP-VERDICT-GENERIC: the split judgment methods (generic domain-neutral vs advertising doctrine) ---------


def test_generic_judgment_method_is_domain_neutral():
    from rag_wright.capabilities.compliance_judgment import generic_judgment_method

    body = generic_judgment_method().lower()
    assert body  # loads
    # the generic method must NOT carry advertising doctrine / vocabulary
    for ad_word in (" ad ", "advertis", "endorsement", "#ad", "puffery", "clinically proven", "substantiation"):
        assert ad_word not in body, f"generic judge SKILL leaked advertising term {ad_word!r}"
    assert "subject" in body and "requirement" in body  # domain-neutral framing


def test_advertising_judgment_method_still_carries_ftc_doctrine():
    from rag_wright.capabilities.compliance_judgment import judgment_method

    body = judgment_method().lower()
    assert "ad text" in body and "disclosure" in body  # advertising doctrine preserved (unchanged)


def test_generic_judge_uses_the_generic_method_advertising_uses_the_ad_method():
    from rag_wright.capabilities.compliance_judgment import build_compliance_judge_fn, build_generic_judge_fn

    seen = {}

    class _Factory:
        def __init__(self, which):
            self._which = which

        def __call__(self, model_id, schema):
            outer = self

            class _R:
                def invoke(self, prompt):
                    seen[outer._which] = prompt
                    return JudgeVerdict(verdict="compliant", rationale="ok", confidence=1.0)

            return _R()

    req = _req(actor="employer")  # neutral actor -> the only advertising terms would come from the SKILL/enrichment
    from rag_wright.contracts.compliance import CheckableFact
    fact = CheckableFact(fact_id="f0", source_doc="s", assertion_text="the subject did X")
    build_generic_judge_fn("m", structured_factory=_Factory("generic"))(fact, req)
    build_compliance_judge_fn("m", structured_factory=_Factory("ad"))(_claim(), req)
    # the generic judge's prompt must NOT contain advertising DOCTRINE; the ad judge's MUST
    gen = seen["generic"].lower()
    for doctrine in ("#ad", "puffery", "clinically proven", "ad text", "substantiation"):
        assert doctrine not in gen, f"generic prompt leaked ad doctrine {doctrine!r}"
    assert "ad text" in seen["ad"].lower()
    assert "CLAIM SIGNALS" in seen["ad"]  # advertising enrichment present only on the ad path
    assert "CLAIM SIGNALS" not in seen["generic"]
