"""CC-2 (compliance §13 C-1/C-2): the `requirement_extraction` capability.

Regulatory section text -> `Requirement[]` (the CC-1 contract). Reuses the docling-graph extraction seam
(`dg_extraction.extract_parties`, generic over `template`) with a new Requirement template, then adapts each
extracted rule to a validated `Requirement`. Same reliability fixes as GP-1B (structured_output=False +
max_tokens cap + a temp-file source), same model seam (Granite via `RAG_SERVING`; ADR-0039). One section in ->
its rules out; the CC-5 ingestion subgraph drives it per FTC section (citation = the section).

The docling-graph TEMPLATE is what the LLM fills (loose strings, robust to model output); the ADAPTER maps it
to the closed CC-1 vocab -- an off-vocab deontic downgrades the rule to AMBIGUOUS, an off-vocab claim_type is
dropped (never fabricated). Provenance (citation) + confidence on every requirement (FR-S.4 / FR-Q.6).
"""

from __future__ import annotations

from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

from rag_wright.capabilities.dg_extraction import edge, extract_parties
from rag_wright.contracts.compliance import ClaimType, Constraint, DeonticType, Requirement
from rag_wright.contracts.provenance import ConfidenceTag

_CLAIM_TYPES = {c.value for c in ClaimType}
_DEONTIC = {d.value for d in DeonticType}


class ExtractedRequirement(BaseModel):
    """One rule the LLM reads out of a regulatory section (a docling-graph child entity). Loose strings by
    design (robust to model output); the adapter maps them to the closed CC-1 vocab."""

    model_config = ConfigDict(graph_id_fields=["requirement_text"], extra="ignore", populate_by_name=True)

    requirement_text: str = Field(
        description="One rule the section states, paraphrased in a single sentence: what must, must not, or may be done")
    deontic_type: str = Field(
        default="obligation",
        description="obligation (must / required), prohibition (must not / may not), or permission (may / allowed)")
    actor: str = Field(default="", description="Who the rule binds, e.g. advertiser, endorser, expert")
    claim_types: list[str] = Field(
        default_factory=list,
        description=("Which advertising claim types this rule applies to, chosen from: efficacy, comparative, "
                     "pricing, health, environmental, endorsement, performance, guarantee"))
    evidence_standard: str = Field(
        default="", description="The substantiation the rule requires, if any (e.g. competent and reliable scientific evidence)")


class ExtractedRegulationSection(BaseModel):
    """A regulatory section and the distinct rules it states (the docling-graph root entity)."""

    model_config = ConfigDict(graph_id_fields=["section"], extra="ignore", populate_by_name=True)

    section: str = Field(description="The section number, e.g. 255.5")
    requirements: list[ExtractedRequirement] = edge(
        "STATES_REQUIREMENT", default_factory=list,
        description="The distinct rules stated in this section (one entry per rule)")


def _coerce_deontic(raw: str) -> tuple[DeonticType, bool]:
    """(DeonticType, ambiguous?) -- map the model's string to the closed vocab; an off-vocab value coerces to
    OBLIGATION and flags AMBIGUOUS (a rule with an unreadable force is kept but marked, never dropped)."""
    value = (raw or "").strip().lower()
    if value in _DEONTIC:
        return DeonticType(value), False
    return DeonticType.OBLIGATION, True


def to_requirements(extracted: ExtractedRegulationSection, *, source: str, section: str) -> list[Requirement]:
    """Adapt an extracted section to validated `Requirement`s. Off-vocab deontic -> AMBIGUOUS; off-vocab
    claim_type dropped; blank rule text skipped. Citation = the section; id = the content-hash scheme."""
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


ExtractFn = Callable[..., Any]  # (text, model, *, template, **kw) -> ExtractedRegulationSection | None


def requirement_extraction(
    text: str, *, model: Any, source: str, section: str,
    extract_fn: ExtractFn = extract_parties, max_tokens: int = 2000, preamble_chars: int = 24_000,
    extraction_contract: str = "auto",
) -> list[Requirement]:
    """Extract the `Requirement`s a regulatory section states. `extract_fn` is the docling-graph seam
    (default `extract_parties`, generic over template); injected in tests. Returns [] if extraction yields
    nothing. `preamble_chars` is wide (a full section, not just a contract preamble).

    `extraction_contract` defaults to "auto" (NOT the seam's contract-tuned "direct"): a regulatory section
    spreads its rules across the whole text, so a single "direct" call silently self-rations and loses most of
    them (measured: §255.5 -> 6 direct vs 31 dense). "auto" picks dense on long sections, direct on short ones.
    See [[docling-graph-extraction-contract]]."""
    extracted = extract_fn(text, model, template=ExtractedRegulationSection,
                           max_tokens=max_tokens, preamble_chars=preamble_chars,
                           extraction_contract=extraction_contract)
    if extracted is None:
        return []
    return to_requirements(extracted, source=source, section=section)


def register_requirement_extraction(registry) -> None:
    """Register `requirement_extraction` (function; CC-2, compliance §13). Contract = `Requirement`."""
    registry.register(
        "requirement_extraction",
        contract=Requirement,
        kind="function",
        display_name="Requirement extraction (regulatory text -> deontic rules)",
    )
