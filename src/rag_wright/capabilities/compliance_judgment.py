"""CC-4 (compliance §13.2): `compliance_judgment` -- the judgment node, the one genuinely new capability.

Per `(claim, applicable_requirement)` -> a `ComplianceFinding`: verdict {compliant, violation, needs_review} +
rationale + BOTH-SIDED citation + confidence. Extends the grounding judge (`spans/semantic_judge.py`,
ADR-0028/0040) from "is X supported?" to "does claim X satisfy/violate requirement Y?". Model-neutral through
the model-profile seam (product = self-hosted Granite, ADR-0039); the `structured_factory` is injected for
hermetic tests. Judge calls parallelize (async + semaphore, per the parallel-LLM rule).

TRUST DESIGN (§13.2):
- Conservative default: a judge failure, an off-vocab verdict, or genuine uncertainty -> `needs_review`, NEVER a
  silent compliant/violation (a false-negative is liability; a false-positive is alert fatigue).
- Both-sided citation comes from the INPUTS (the claim's assertion span + the requirement's section), not the
  LLM -- the model rules, but the citations are ground truth.
- Every violation/needs_review is human-gated (`ComplianceFinding.needs_human_review`).

The Flash->Pro escalation tier and rule-anchored deterministic pre-checks (§13.2) are designed extension points
on this seam; the core here is the single Granite judge + the conservative mapping.
"""

from __future__ import annotations

import os
from typing import Callable, Optional

from pydantic import BaseModel

from rag_wright.contracts.compliance import Claim, ComplianceFinding, Requirement, Verdict
from rag_wright.models.seam import build_structured
from rag_wright.util.concurrent import map_concurrent

_VERDICTS = {v.value for v in Verdict}


class JudgeVerdict(BaseModel):
    """The raw structured output of one judge call. `verdict` is a loose string mapped to the closed `Verdict`
    by the capability (an unreadable value -> needs_review); citations are added from the inputs, not here."""

    verdict: str
    rationale: str = ""
    confidence: float = 0.0


# judge_fn: (claim, requirement) -> JudgeVerdict, or None if the judge could not rule (-> conservative default).
JudgeFn = Callable[[Claim, Requirement], Optional[JudgeVerdict]]

_PROMPT = (
    "You are an advertising-compliance auditor. Judge the ADVERTISING CLAIM against the REGULATORY REQUIREMENT. "
    "You can see ONLY the ad text -- you CANNOT see the advertiser's evidence/studies. Answer one verdict:\n"
    "- violation: reserve for what is CLEARLY wrong from the text ITSELF -- (a) the claim OVERCLAIMS proof "
    "('clinically proven', 'scientifically proven', 'science backed', 'guaranteed', 'doctor proven') WITHOUT "
    "pointing to an actual study/data; (b) an endorsement is missing a required DISCLOSURE (no '#ad' / 'paid "
    "partnership' in disclosures_present); or (c) a review/testimonial is fake or deceptive.\n"
    "- needs_review: an OBJECTIVE efficacy / health / performance / factual claim that may well be true but the "
    "ad shows NO evidence and makes NO overclaim -- you cannot verify its substantiation from the text alone, so "
    "ESCALATE it for a human to check the advertiser's substantiation file. Do NOT call this a violation (you do "
    "not have the evidence) and do NOT clear it as compliant (you cannot confirm it either).\n"
    "- compliant: the claim is mere SUBJECTIVE opinion or taste/experience PUFFERY ('smooth flavor', 'relaxing', "
    "'I like it'); OR the required DISCLOSURE is present (disclosures_present, e.g. '#ad'); OR the ad actually "
    "POINTS TO real evidence (a specific study/data/citation) for an objective claim; OR there is no objective "
    "claim to substantiate.\n"
    "Only 'violation' when the text clearly shows the breach; only 'compliant' when the text clearly clears it; "
    "otherwise 'needs_review'. Give a one-sentence rationale and a confidence in [0,1].\n\n"
    "REQUIREMENT ({deontic}, {citation}; applies to {actor}):\n{requirement_text}\n\n"
    "CLAIM (type={claim_type}; disclosures_present={disclosures}; evidence_referenced={evidence}):\n{assertion}"
)


def build_compliance_judge_fn(model_id: str, *, structured_factory=build_structured) -> JudgeFn:
    """Wire a Granite-backed judge `JudgeFn` through the model seam (product = vLLM-Granite; ADR-0039).
    `structured_factory` is injected for hermetic testing. Both sides go into the prompt; the claim's
    disclosure/evidence signals are surfaced so the model can rule on lexically-anchored rules."""

    def judge(claim: Claim, requirement: Requirement) -> Optional[JudgeVerdict]:
        prompt = _PROMPT.format(
            deontic=requirement.deontic_type.value,
            citation=requirement.citation,
            actor=requirement.actor,
            requirement_text=requirement.requirement_text,
            claim_type=claim.claim_type.value,
            disclosures=claim.disclosures_present or "none",
            evidence=claim.evidence_referenced,
            assertion=claim.assertion_text,
        )
        return structured_factory(model_id, JudgeVerdict).invoke(prompt)

    return judge


def _to_verdict(raw: str) -> Verdict:
    """Map the LLM's verdict string to the closed vocab; an unreadable value -> NEEDS_REVIEW (conservative)."""
    value = (raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    return Verdict(value) if value in _VERDICTS else Verdict.NEEDS_REVIEW


def compliance_judgment(claim: Claim, requirement: Requirement, *, judge_fn: JudgeFn) -> ComplianceFinding:
    """Judge one `(claim, requirement)` pair -> a `ComplianceFinding`. A None verdict (judge failure) or an
    off-vocab verdict conservatively defaults to `needs_review`. Citations are taken from the inputs."""
    ruling = judge_fn(claim, requirement)
    if ruling is None:
        verdict, rationale, confidence = Verdict.NEEDS_REVIEW, "judge did not return a ruling", 0.0
    else:
        verdict = _to_verdict(ruling.verdict)
        rationale = ruling.rationale
        confidence = min(1.0, max(0.0, ruling.confidence))
    return ComplianceFinding(
        claim_id=claim.claim_id,
        requirement_id=requirement.requirement_id,
        verdict=verdict,
        rationale=rationale,
        citation_claim=f"{claim.source_doc}: {claim.assertion_text}",
        citation_requirement=f"{requirement.citation} ({requirement.requirement_id}): {requirement.requirement_text}",
        confidence=confidence,
    )


_JUDGE_TIMEOUT_S = float(os.environ.get("RAG_JUDGE_TIMEOUT_S", "90"))  # per-pair wall-clock bound (LLM-CALL-TIMEOUT)


def judge_pairs(
    pairs: list[tuple[Claim, Requirement]], *, judge_fn: JudgeFn, max_concurrency: int = 8,
    timeout_s: float | None = _JUDGE_TIMEOUT_S,
) -> list[ComplianceFinding]:
    """Judge many `(claim, requirement)` pairs concurrently (async + semaphore, per the parallel-LLM rule).
    Order is preserved. CC-6 drives this over a subject doc's claims x their applicable requirements.

    `timeout_s` (LLM-CALL-TIMEOUT) bounds each judgment with a hard wall-clock deadline so a stalled provider
    response never hangs the batch; a timed-out pair (map_concurrent -> None) becomes a conservative
    needs_review finding (the same conservative default as a judge that could not rule)."""
    results = map_concurrent(
        pairs, lambda pair: compliance_judgment(pair[0], pair[1], judge_fn=judge_fn),
        max_concurrency=max_concurrency, timeout_s=timeout_s,
    )
    return [
        result if result is not None
        else compliance_judgment(claim, requirement, judge_fn=lambda _c, _r: None)  # timed out -> needs_review
        for (claim, requirement), result in zip(pairs, results)
    ]


def register_compliance_judgment(registry) -> None:
    """Register `compliance_judgment` (function; CC-4, compliance §13.2). Contract = `ComplianceFinding`."""
    registry.register(
        "compliance_judgment",
        contract=ComplianceFinding,
        kind="function",
        display_name="Compliance judgment (claim x requirement -> cited verdict)",
    )
