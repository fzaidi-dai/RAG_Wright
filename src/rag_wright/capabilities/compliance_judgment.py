"""CC-4 (compliance §13.2), SKILL-SPLIT: the judgment node, split into a SKILL + a deterministic FUNCTION.

Per the capability-architecture principle (a `function` is deterministic and takes no model; a single LLM act is
an authored `agent_skill`; a workflow is a `subgraph`), the judgment is two capabilities:

- **`compliance_judgment` (agent_skill)** -- the LLM judgment METHOD, authored as `skills/compliance_judgment/
  SKILL.md` and applied through the model seam (product = Granite, ADR-0039). Given one claim + one requirement
  and ONLY the ad text, it returns a raw `JudgeVerdict` (verdict / rationale / confidence). `build_compliance_
  judge_fn` is its runtime; `structured_factory` is injected for hermetic tests.
- **`compliance_finding_assembly` (function)** -- `assemble_finding`: DETERMINISTIC, no model. Maps the raw
  verdict to the closed vocab (unreadable/missing -> needs_review, the conservative default), attaches the
  BOTH-SIDED citation FROM THE INPUTS (the model never authors a citation), and returns the `ComplianceFinding`.

`compliance_judgment(...)` composes them (skill -> function) for callers; `judge_pairs` runs the composition
concurrently (async + semaphore + LLM-CALL-TIMEOUT). The applying capability owns the guarantees the skill does
not (verdict vocab, conservative default, citation) -- the SKILL.md teaches only the reading.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional

from pydantic import BaseModel

from rag_wright.contracts.compliance import CheckableFact, Claim, ComplianceFinding, Requirement, Verdict
from rag_wright.models.seam import build_structured
from rag_wright.util.concurrent import map_concurrent

_VERDICTS = {v.value for v in Verdict}
_SKILL_PATH = Path(__file__).parents[1] / "skills" / "compliance_judgment" / "SKILL.md"


class JudgeVerdict(BaseModel):
    """The raw structured output of one judge call (the `compliance_judgment` SKILL's typed output). `verdict`
    is a loose string mapped to the closed `Verdict` by the FUNCTION (an unreadable value -> needs_review);
    citations are added from the inputs by the function, never authored here."""

    verdict: str
    rationale: str = ""
    confidence: float = 0.0


# judge_fn: (subject_fact, requirement) -> JudgeVerdict, or None if the judge could not rule (-> conservative
# default). Accepts any `CheckableFact` (the advertising `Claim` is one).
JudgeFn = Callable[[CheckableFact, Requirement], Optional[JudgeVerdict]]

# The per-call appendix bound onto the SKILL method (the static method teaches the reading; the specific
# requirement + subject are appended at call time, the okf_navigate `_with_question` pattern).
# COMP-VERDICT-GENERIC: split into a domain-agnostic BASE tail (requirement + subject assertion -- works for ANY
# CheckableFact / domain) + an ADVERTISING enrichment line (claim_type / disclosures / evidence). The generic
# judge uses only the base; the advertising judge appends the enrichment (behavior unchanged).
_BASE_PROMPT_TAIL = (
    "\n\nREQUIREMENT ({deontic}, {citation}; applies to {actor}):\n{requirement_text}\n\n"
    "SUBJECT:\n{assertion}"
)
_AD_ENRICHMENT = "\n\nCLAIM SIGNALS: type={claim_type}; disclosures_present={disclosures}; evidence_referenced={evidence}"


def judgment_method() -> str:
    """The compliance-judgment method (the `compliance_judgment` SKILL body, YAML frontmatter stripped) used as
    the judge's system/method prompt. Authored knowledge (skills/compliance_judgment/SKILL.md), not hardcoded."""
    text = _SKILL_PATH.read_text(encoding="utf-8")
    if text.startswith("---"):
        marker = text.find("\n---", 3)
        if marker != -1:
            text = text[marker + 4 :]
    return text.strip()


def _base_tail(fact: CheckableFact, requirement: Requirement) -> str:
    """The domain-agnostic judge appendix: the requirement + the subject assertion. Works for ANY CheckableFact."""
    return _BASE_PROMPT_TAIL.format(
        deontic=requirement.deontic_type.value, citation=requirement.citation, actor=requirement.actor,
        requirement_text=requirement.requirement_text, assertion=fact.assertion_text)


def build_generic_judge_fn(model_id: str, *, structured_factory=build_structured) -> JudgeFn:
    """COMP-VERDICT-GENERIC: the DOMAIN-AGNOSTIC judge -- rules a `(subject_fact, requirement)` pair on TEXT alone
    (the base tail; no advertising claim signals), so it gives a verdict in ANY compliance domain. Same SKILL
    method + conservative default as the advertising judge; only the appendix differs."""
    method = judgment_method()

    def judge(fact: CheckableFact, requirement: Requirement) -> Optional[JudgeVerdict]:
        return structured_factory(model_id, JudgeVerdict).invoke(method + _base_tail(fact, requirement))

    return judge


def build_compliance_judge_fn(model_id: str, *, structured_factory=build_structured) -> JudgeFn:
    """The advertising `compliance_judgment` SKILL runtime: a Granite-backed judge `JudgeFn` through the model seam
    (product = vLLM-Granite; ADR-0039). Base tail (requirement + subject) + the ADVERTISING claim signals
    (claim_type / disclosures / evidence). Behavior unchanged from before the CheckableFact split.
    `structured_factory` is injected for tests."""
    method = judgment_method()

    def judge(claim: Claim, requirement: Requirement) -> Optional[JudgeVerdict]:
        prompt = method + _base_tail(claim, requirement) + _AD_ENRICHMENT.format(
            claim_type=claim.claim_type.value,
            disclosures=claim.disclosures_present or "none",
            evidence=claim.evidence_referenced,
        )
        return structured_factory(model_id, JudgeVerdict).invoke(prompt)

    return judge


def _to_verdict(raw: str) -> Verdict:
    """Map the LLM's verdict string to the closed vocab; an unreadable value -> NEEDS_REVIEW (conservative)."""
    value = (raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    return Verdict(value) if value in _VERDICTS else Verdict.NEEDS_REVIEW


def assemble_finding(
    claim: Claim, requirement: Requirement, ruling: Optional[JudgeVerdict]
) -> ComplianceFinding:
    """`compliance_finding_assembly` (FUNCTION -- deterministic, no model): map the skill's raw `JudgeVerdict`
    (or None) to a `ComplianceFinding`. None or an off-vocab verdict conservatively defaults to `needs_review`;
    the BOTH-SIDED citation is taken from the INPUTS (the model never authors a citation)."""
    if ruling is None:
        verdict, rationale, confidence = Verdict.NEEDS_REVIEW, "judge did not return a ruling", 0.0
    else:
        verdict = _to_verdict(ruling.verdict)
        rationale = ruling.rationale
        confidence = min(1.0, max(0.0, ruling.confidence))
    return ComplianceFinding(
        claim_id=claim.fact_id,
        requirement_id=requirement.requirement_id,
        verdict=verdict,
        rationale=rationale,
        citation_claim=f"{claim.source_doc}: {claim.assertion_text}",
        citation_requirement=f"{requirement.citation} ({requirement.requirement_id}): {requirement.requirement_text}",
        confidence=confidence,
    )


def compliance_judgment(claim: Claim, requirement: Requirement, *, judge_fn: JudgeFn) -> ComplianceFinding:
    """Compose the SKILL (LLM judgment) and the FUNCTION (deterministic assembly): run `judge_fn` then
    `assemble_finding`. A None ruling conservatively defaults to `needs_review`. Citations come from the inputs."""
    return assemble_finding(claim, requirement, judge_fn(claim, requirement))


_JUDGE_TIMEOUT_S = float(os.environ.get("RAG_JUDGE_TIMEOUT_S", "90"))  # per-pair wall-clock bound (LLM-CALL-TIMEOUT)


def judge_pairs(
    pairs: list[tuple[Claim, Requirement]], *, judge_fn: JudgeFn, max_concurrency: int = 8,
    timeout_s: float | None = _JUDGE_TIMEOUT_S,
) -> list[ComplianceFinding]:
    """Run the skill->function composition over many `(claim, requirement)` pairs concurrently (async +
    semaphore, per the parallel-LLM rule). Order is preserved. CC-6 drives this over a subject doc's claims x
    their applicable requirements.

    `timeout_s` (LLM-CALL-TIMEOUT) bounds each judgment with a hard wall-clock deadline so a stalled provider
    response never hangs the batch; a timed-out pair (map_concurrent -> None) becomes a conservative
    needs_review finding (the same conservative default as a judge that could not rule)."""
    results = map_concurrent(
        pairs, lambda pair: compliance_judgment(pair[0], pair[1], judge_fn=judge_fn),
        max_concurrency=max_concurrency, timeout_s=timeout_s,
    )
    return [
        result if result is not None
        else assemble_finding(claim, requirement, None)  # timed out -> conservative needs_review
        for (claim, requirement), result in zip(pairs, results)
    ]


def register_compliance_judgment(registry) -> None:
    """Register `compliance_judgment` as an AGENT_SKILL (CC-4): a single grounded LLM judgment act, authored as
    `skills/compliance_judgment/SKILL.md` and applied via the seam. Typed output = `JudgeVerdict`."""
    registry.register(
        "compliance_judgment",
        contract=JudgeVerdict,
        kind="agent_skill",
        display_name="Compliance judgment (claim x requirement -> verdict; authored skill)",
    )


def register_compliance_finding_assembly(registry) -> None:
    """Register `compliance_finding_assembly` (FUNCTION -- deterministic): the skill's raw verdict + the inputs
    -> a cited `ComplianceFinding` (conservative default, both-sided citation from the inputs)."""
    registry.register(
        "compliance_finding_assembly",
        contract=ComplianceFinding,
        kind="function",
        display_name="Compliance finding assembly (verdict + inputs -> cited finding)",
    )
