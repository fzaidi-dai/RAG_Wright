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
    build_compliance_judge_fn,
    compliance_judgment,
    judge_pairs,
    register_compliance_judgment,
)
from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.contracts.compliance import (
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
    base.setdefault("claim_id", Claim.make_id(base["source_doc"], 0, base["assertion_text"]))
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
    assert finding.claim_id == claim.claim_id and finding.requirement_id == req.requirement_id
    assert claim.assertion_text in finding.citation_claim
    assert req.citation in finding.citation_requirement  # "§ 255.5"


# --- batch: judge many pairs concurrently --------------------------------------------------------


def test_judge_pairs_returns_one_finding_per_pair():
    pairs = [(_claim(), _req()), (_claim(assertion_text="lose 30 lbs"), _req(citation="§ 255.2"))]
    findings = judge_pairs(pairs, judge_fn=lambda c, r: JudgeVerdict(verdict="needs_review", rationale="", confidence=0.0))
    assert len(findings) == 2 and all(f.verdict is Verdict.NEEDS_REVIEW for f in findings)


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


def test_registers_as_a_function():
    reg = CapabilityRegistry()
    register_compliance_judgment(reg)
    entry = reg.get("compliance_judgment")
    assert entry.kind == "function" and entry.contract is ComplianceFinding


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
