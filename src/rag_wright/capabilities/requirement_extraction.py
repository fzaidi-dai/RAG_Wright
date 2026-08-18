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

from typing import Any, Callable

from rag_wright.capabilities.dg_extraction import aextract_parties, extract_parties
from rag_wright.contracts.compliance import ClaimType, Constraint, DeonticType, Requirement
from rag_wright.contracts.provenance import ConfidenceTag
from rag_wright.skills.requirement_extraction.template import (  # the skill's schema asset
    ExtractedRegulationSection,
    ExtractedRequirement,
)

__all__ = ["ExtractedRegulationSection", "ExtractedRequirement", "extract_regulation_section",
           "to_requirements", "register_requirement_adaptation"]

_CLAIM_TYPES = {c.value for c in ClaimType}
_DEONTIC = {d.value for d in DeonticType}


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
        scope = [Constraint(dimension="claim_type", value=ct.strip().lower())
                 for ct in item.claim_types if ct.strip().lower() in _CLAIM_TYPES]
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
