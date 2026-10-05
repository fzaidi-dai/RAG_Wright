"""CC-2 (compliance §13), SKILL-SPLIT: the extraction ACT + the adaptation FUNCTION for `requirement_extraction`.

Per the capability-architecture rubric, `requirement_extraction` is a SUBGRAPH (its `auto/dense` extraction is
MULTI-LLM-call; the extract -> adapt chaining is the deterministic workflow). This module holds the subgraph's
two pieces:

- **the extraction ACT** (`extract_regulation_section`) -- the docling-graph schema-driven extraction using the
  co-located skill asset `skills/requirement_extraction/template.py` (ExtractedRegulationSection). Runs `"auto"`
  (dense on long sections, skeleton-then-fill). The subgraph's extract node.
- **`requirement_adaptation` (function)** -- `to_requirements`: DETERMINISTIC, no model. Maps the raw extraction
  to validated `Requirement`s (deontic vocab coercion -> AMBIGUOUS; off-vocab claim_type dropped; blank skipped;
  citation = the section; content-hash id). The subgraph's adapt node.

The subgraph itself (extract -> adapt, hardened) is `subgraphs/requirement_extraction.py`. The template lives
with the skill (an Agent-Skill asset), re-exported here for consumers/tests.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from rag_wright.capabilities.dg_extraction import aextract_parties, extract_parties
from rag_wright.contracts.compliance import ClaimType, Constraint, DeonticType, Requirement
from rag_wright.contracts.provenance import ConfidenceTag
from rag_wright.skills.requirement_extraction.template import (  # the skill's schema asset
    ExtractedRegulationSection,
    ExtractedRequirement,
)

__all__ = ["ExtractedRegulationSection", "ExtractedRequirement", "extract_regulation_section",
           "ajev_extract_regulation_section", "operative_rule_spans", "to_requirements",
           "register_requirement_adaptation"]


def operative_rule_spans(text: str) -> list[tuple[str, str]]:
    """CIC (ADR-0119 scaffolding): deterministically split a section's text into candidate rule spans (reusing the
    engine's byte-faithful `segment_clause`), keep the OPERATIVE ones (carrying a deontic cue), and derive each
    span's deontic_type from its cue (ttl-driven `deontic_type_of`). Returns `[(verbatim_span_text,
    deontic_local_name)]`. NO LLM. This is the span-producer front end a decision model (`jev_decision`, ADR-0119)
    judges per span; it is NOT wired into the live ingest extraction, which is unchanged."""
    from rag_wright.ontology.loader import deontic_type_of
    from rag_wright.spans.segment import segment_clause

    out: list[tuple[str, str]] = []
    for span in segment_clause("", text or ""):
        span_text = span.text.strip()
        deontic = deontic_type_of(span_text)
        if deontic is not None:
            out.append((span_text, deontic))
    return out

_CLAIM_TYPES = {c.value for c in ClaimType}
_DEONTIC = {d.value for d in DeonticType}

# --- CIC (ADR-0119): the Jev-decision extraction act -- deterministic spans + ONE Jev call/span (operative gate +
# claim_types + actor), cue-deontic, verbatim text. The question-sets are compliance-domain knowledge (they mirror
# the ttl ClaimType/ActorRole vocabs; a later move into the .ttl per ADR-0066 is a documented follow-up). The open
# fields applicability + evidence_standard are NOT produced here (open-vocab; a residual-LLM/ttl follow-up) -- for
# FTC the claim_types ARE the applicability (claim_type constraints via `to_requirements`).
_CLAIM_CRITERIA = {
    "efficacy": "the product WORKS / is effective at its purpose (cures, treats, achieves the promised result)",
    "comparative": "COMPARES the product to competitors or alternatives",
    "pricing": "PRICE, discount, 'reduced', 'free', or savings",
    "health": "HEALTH, medical, disease, nutrition, or bodily-safety",
    "environmental": "ENVIRONMENTAL / green / eco / sustainable",
    "endorsement": "ENDORSEMENTS, testimonials, reviews, or who may endorse",
    "performance": "measurable HOW-WELL -- speed, durability, strength, capacity (not merely that it works)",
    "guarantee": "a GUARANTEE, warranty, or money-back promise",
}
_ACTOR_ROLES = {
    "advertiser": "the advertiser / marketer / manufacturer / brand making the claim",
    "endorser": "the endorser / testimonial-giver / reviewer / influencer",
    "expert": "an expert whose evaluation is relied upon",
    "seller": "the seller / vendor / retailer, when named distinctly",
    "consumer": "the consumer / customer / audience",
    "employer": "the employer (workplace safety / injury recordkeeping)",
    "other": "none of the above, multiple parties, or unspecified",
}
_JEV_GUIDANCE = (
    "Guidance: 'This supplement cures insomnia' -> efficacy, health; 'lasts 3x longer than Brand X' -> "
    "performance, comparative; 'reduced to $9.99' -> pricing; 'as recommended by Dr. Smith' -> endorsement. "
    "Actor: who the rule binds (advertiser/endorser/expert/seller/consumer/employer).\n\nRule:\n")


def _jev_questions() -> dict:
    q = {"operative": {"type": "noul",
                       "instructions": "Is this sentence itself a BINDING operative rule (obligation/prohibition/"
                                       "permission or a binding liability/conditional consequence)?",
                       "criteria": {"true": "it binds conduct directly",
                                    "false": "an illustrative example, definition, scope, cross-reference, negated "
                                             "requirement, or descriptive remark"}},
         "actor": {"type": "choice", "instructions": "Who does this rule primarily bind?", "criteria": _ACTOR_ROLES}}
    for ct, desc in _CLAIM_CRITERIA.items():
        q[f"ct_{ct}"] = {"type": "noul", "instructions": f"Does this rule apply to {ct} advertising claims?",
                         "criteria": {"true": desc, "false": f"not specifically about {ct}"}}
    return q


async def ajev_extract_regulation_section(
    text: str, *, concurrency: int = 8, op_threshold: float = 0.5, ct_threshold: float = 0.6,
) -> ExtractedRegulationSection:
    """CIC (ADR-0119): the Jev-decision extraction act. Deterministic `operative_rule_spans` produces candidate
    spans; ONE Jev call per span (routed through the capability layer, `jev_decision`) decides operative-gate +
    actor + claim_types; deontic_type is the cue-rule; requirement_text is the verbatim span. Drops spans the Jev
    operative gate rejects (< `op_threshold`). applicability/evidence_standard are left empty here (see note)."""
    from rag_wright.capabilities.invoke import capability_impl

    spans = operative_rule_spans(text)
    if not spans:
        return ExtractedRegulationSection(section="", requirements=[])
    jev = capability_impl("jev_decision")  # async (resources, inputs) -> decision body; store-independent
    questions = _jev_questions()
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _one(span_text: str, deontic: str) -> ExtractedRequirement | None:
        async with sem:
            d = await jev(None, {"state": _JEV_GUIDANCE + span_text, "questions": questions})
        ans = d["answers"]
        if float(ans["operative"].get("noul", 0.0)) < op_threshold:
            return None  # Jev gate: not a binding rule -> drop (refines the cue-presence gate's precision)
        claim_types = [ct for ct in _CLAIM_CRITERIA if float(ans[f"ct_{ct}"].get("noul", 0.0)) >= ct_threshold]
        return ExtractedRequirement(requirement_text=span_text, deontic_type=deontic,
                                    actor=str(ans["actor"].get("choice", "")), claim_types=claim_types,
                                    applicability=[], evidence_standard="")
    reqs = [r for r in await asyncio.gather(*(_one(t, d) for t, d in spans)) if r is not None]
    return ExtractedRegulationSection(section="", requirements=reqs)


def _coerce_deontic(raw: str) -> tuple[DeonticType, bool]:
    """(DeonticType, ambiguous?) -- map the model's string to the closed vocab; an off-vocab value coerces to
    OBLIGATION and flags AMBIGUOUS (a rule with an unreadable force is kept but marked, never dropped)."""
    value = (raw or "").strip().lower()
    if value in _DEONTIC:
        return DeonticType(value), False
    return DeonticType.OBLIGATION, True


ExtractFn = Callable[..., Any]  # (text, model, *, template, **kw) -> ExtractedRegulationSection | None


def extract_regulation_section(
    text: str, *, model: Any, extract_fn: ExtractFn = extract_parties,
    max_tokens: int = 2000, preamble_chars: int = 24_000, extraction_contract: str = "auto",
) -> ExtractedRegulationSection | None:
    """The extraction ACT (the requirement_extraction skill, docling-graph runtime): fill the skill's
    `template.py` schema from a § section's text. `extraction_contract="auto"` -> dense (multi-call) on long
    sections so rules are not silently self-rationed. `extract_fn` is injected for hermetic tests."""
    return extract_fn(text, model, template=ExtractedRegulationSection,
                      max_tokens=max_tokens, preamble_chars=preamble_chars,
                      extraction_contract=extraction_contract)


async def aextract_regulation_section(
    text: str, *, model: Any, aextract_fn: Any = aextract_parties,
    max_tokens: int = 2000, preamble_chars: int = 24_000, extraction_contract: str = "auto",
) -> ExtractedRegulationSection | None:
    """ASYNC (ADR-0057): the async twin of `extract_regulation_section` -- docling-graph extraction on the async
    seam (`aextract_parties`, true wall-clock deadline via the injected client). `aextract_fn` injected for tests."""
    return await aextract_fn(text, model, template=ExtractedRegulationSection,
                             max_tokens=max_tokens, preamble_chars=preamble_chars,
                             extraction_contract=extraction_contract)


def to_requirements(extracted: ExtractedRegulationSection, *, source: str, section: str) -> list[Requirement]:
    """`requirement_adaptation` (FUNCTION -- deterministic, no model): adapt an extracted section to validated
    `Requirement`s. Off-vocab deontic -> AMBIGUOUS; off-vocab claim_type dropped; blank rule text skipped.
    Citation = the section; id = the content-hash scheme."""
    out: list[Requirement] = []
    for item in extracted.requirements:
        text = (item.requirement_text or "").strip()
        if not text:
            continue
        deontic, ambiguous = _coerce_deontic(item.deontic_type)
        # claim_type is the ADVERTISING reference (closed FTC vocab -> off-vocab dropped, unchanged).
        scope = [Constraint(dimension="claim_type", value=ct.strip().lower())
                 for ct in item.claim_types if ct.strip().lower() in _CLAIM_TYPES]
        # P3a (Gap 2): generic 'dimension: value' applicability conditions for ANY policy domain -- kept
        # RECALL-FIRST (an unknown customer dimension is NOT dropped; the query-side matcher is dimension-agnostic).
        seen = {c.as_tuple() for c in scope}
        for cond in getattr(item, "applicability", None) or []:
            dim, sep, val = (cond or "").partition(":")
            dim, val = dim.strip().lower(), val.strip().lower()
            if not (sep and dim and val) or (dim, val) in seen:
                continue
            seen.add((dim, val))
            scope.append(Constraint(dimension=dim, value=val))
        out.append(Requirement(
            requirement_id=Requirement.make_id(source, section, text),
            source=source,
            citation=f"§ {section}",
            deontic_type=deontic,
            actor=(item.actor or "").strip() or "unspecified",
            applicability_scope=scope,
            requirement_text=text,
            evidence_standard=(item.evidence_standard or "").strip() or None,
            confidence=ConfidenceTag.AMBIGUOUS if ambiguous else ConfidenceTag.EXTRACTED,
        ))
    return out


def register_requirement_adaptation(registry) -> None:
    """Register `requirement_adaptation` (FUNCTION -- deterministic): the raw ExtractedRegulationSection ->
    validated `Requirement[]` (deontic coercion, off-vocab handling, citation, content-hash id). No model."""
    registry.register(
        "requirement_adaptation",
        contract=Requirement,
        kind="function",
        display_name="Requirement adaptation (extracted section -> validated Requirements)",
    )
