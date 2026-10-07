"""CC-3 (compliance §13 C-3), SKILL-SPLIT: subject ad -> `Claim[]`, split into a SKILL + a FUNCTION.

Per the capability-architecture principle (`function` = deterministic/no-model; a single LLM act = an authored
`agent_skill`; a workflow = `subgraph`), this is two capabilities:

- **`claim_extraction` (agent_skill)** -- the claim-extraction METHOD, authored as `skills/claim_extraction/`
  (SKILL.md + the `template.py` schema asset: `ExtractedAd`/`ExtractedClaim`). One docling-graph call (`direct`,
  ads are short) fills the template. `extract_ad` is its runtime; `extract_fn` is injected for hermetic tests.
- **`claim_adaptation` (function)** -- `to_claims`: DETERMINISTIC, no model. Maps the raw `ExtractedAd` to the
  closed CC-1 `Claim` vocab (off-vocab claim_type kept-but-AMBIGUOUS -- a checkable assertion is never dropped),
  attaches the span provenance + content-hash `claim_id`.

`claim_extraction(...)` composes them (skill -> function) for callers. The schema lives with the skill (an
Agent-Skill asset), re-exported here for consumers/tests.
"""

from __future__ import annotations

from typing import Any, Callable

from rag_wright.packs.contracts.capabilities.dg_extraction import aextract_parties, extract_parties
from rag_wright.packs.compliance.schemas.compliance import Claim, ClaimType
from rag_wright.contracts.provenance import ConfidenceTag
from rag_wright.packs.compliance.skills.claim_extraction.template import ExtractedAd, ExtractedClaim  # the skill's schema asset

__all__ = ["ExtractedAd", "ExtractedClaim", "extract_ad", "aextract_ad", "to_claims", "claim_extraction",
           "aclaim_extraction", "register_claim_extraction", "register_claim_adaptation"]

_CLAIM_TYPES = {c.value for c in ClaimType}
# off-vocab fallback: keep the claim (never drop a checkable assertion) but mark it AMBIGUOUS. EFFICACY is the
# broadest "the product works" type; the AMBIGUOUS tag is what actually signals the uncertainty downstream.
_FALLBACK_CLAIM_TYPE = ClaimType.EFFICACY


def _coerce_claim_type(raw: str) -> tuple[ClaimType, bool]:
    """(ClaimType, ambiguous?) -- map the model's string to the closed vocab; an off-vocab value keeps the
    claim under the fallback type and flags AMBIGUOUS (never drop a real assertion)."""
    value = (raw or "").strip().lower()
    if value in _CLAIM_TYPES:
        return ClaimType(value), False
    return _FALLBACK_CLAIM_TYPE, True


def _clean(v: str) -> str | None:
    v = (v or "").strip()
    return v or None


def to_claims(extracted: ExtractedAd, *, source_doc: str) -> list[Claim]:
    """`claim_adaptation` (FUNCTION -- deterministic, no model): adapt an extracted ad to validated `Claim`s.
    Off-vocab claim_type -> fallback + AMBIGUOUS; blank assertion skipped. `claim_id` uses the content-hash
    scheme; the (source_doc, assertion) is the span cite."""
    from rag_wright.packs.compliance.schemas.compliance import Constraint

    out: list[Claim] = []
    for index, item in enumerate(extracted.claims):
        text = (item.assertion_text or "").strip()
        if not text:
            continue
        claim_type, ambiguous = _coerce_claim_type(item.claim_type)
        actor = _clean(item.actor)
        # DEON-8: surface the ROLE actor as a dimension-agnostic Constraint on `.scope` (mirroring the generic
        # `to_facts`), so the obligation actor gate (DEON-7) works on the ad path too. The typed `.actor` is kept.
        scope = [Constraint(dimension="actor", value=actor.lower())] if actor else []
        out.append(Claim(
            fact_id=Claim.make_id(source_doc, index, text),
            source_doc=source_doc,
            claim_type=claim_type,
            assertion_text=text,
            scope=scope,
            actor=actor,
            subject_product=_clean(item.subject_product),
            quantitative_value=_clean(item.quantitative_value),
            disclosures_present=[d.strip() for d in item.disclosures_present if d and d.strip()],
            evidence_referenced=bool(item.evidence_referenced),
            medium=_clean(item.medium),
            confidence=ConfidenceTag.AMBIGUOUS if ambiguous else ConfidenceTag.EXTRACTED,
        ))
    return out


ExtractFn = Callable[..., Any]  # (text, model, *, template, **kw) -> ExtractedAd | None


def extract_ad(
    text: str, *, model: Any, extract_fn: ExtractFn = extract_parties,
    max_tokens: int = 1500, preamble_chars: int = 8000, extraction_contract: str = "direct",
) -> ExtractedAd | None:
    """The `claim_extraction` SKILL's runtime: run the docling-graph extraction (the skill's `template.py`
    schema) through the model seam and return the raw `ExtractedAd` (or None). Ads are short -> `direct`."""
    return extract_fn(text, model, template=ExtractedAd,
                      max_tokens=max_tokens, preamble_chars=preamble_chars,
                      extraction_contract=extraction_contract)


def claim_extraction(
    text: str, *, model: Any, source_doc: str,
    extract_fn: ExtractFn = extract_parties, max_tokens: int = 1500, preamble_chars: int = 8000,
    extraction_contract: str = "direct",
) -> list[Claim]:
    """Compose the SKILL (extract_ad) and the FUNCTION (to_claims): extract the raw ad, then adapt to `Claim`s.
    Returns [] if extraction yields nothing."""
    extracted = extract_ad(text, model=model, extract_fn=extract_fn, max_tokens=max_tokens,
                           preamble_chars=preamble_chars, extraction_contract=extraction_contract)
    if extracted is None:
        return []
    return to_claims(extracted, source_doc=source_doc)


async def aextract_ad(
    text: str, *, model: Any, aextract_fn: Any = aextract_parties,
    max_tokens: int = 1500, preamble_chars: int = 8000, extraction_contract: str = "direct",
) -> ExtractedAd | None:
    """ASYNC-C1 (ADR-0057): the async twin of `extract_ad` -- the claim-extraction docling-graph act on the async
    seam (`aextract_parties`, true wall-clock deadline via the injected client). `aextract_fn` injected for tests."""
    return await aextract_fn(text, model, template=ExtractedAd,
                             max_tokens=max_tokens, preamble_chars=preamble_chars,
                             extraction_contract=extraction_contract)


async def aclaim_extraction(
    text: str, *, model: Any, source_doc: str, aextract_fn: Any = aextract_parties,
    max_tokens: int = 1500, preamble_chars: int = 8000, extraction_contract: str = "direct",
) -> list[Claim]:
    """ASYNC-C1 (ADR-0057): the async twin of `claim_extraction` -- await the async extraction ACT, then the
    deterministic `to_claims` adaptation. Same contract: [] if extraction yields nothing."""
    extracted = await aextract_ad(text, model=model, aextract_fn=aextract_fn, max_tokens=max_tokens,
                                  preamble_chars=preamble_chars, extraction_contract=extraction_contract)
    if extracted is None:
        return []
    return to_claims(extracted, source_doc=source_doc)


def register_claim_extraction(registry) -> None:
    """Register `claim_extraction` as an AGENT_SKILL (CC-3): a single docling-graph LLM extraction act, authored
    as `skills/claim_extraction/` (SKILL.md + the template.py schema). Typed output = `ExtractedAd`."""
    registry.register(
        "claim_extraction",
        contract=ExtractedAd,
        kind="agent_skill",
        display_name="Claim extraction (subject ad -> checkable claims; authored skill)",
    )


def register_claim_adaptation(registry) -> None:
    """Register `claim_adaptation` (FUNCTION -- deterministic): the skill's raw `ExtractedAd` -> validated
    `Claim[]` (closed vocab, off-vocab kept-but-AMBIGUOUS, span provenance, content-hash id)."""
    registry.register(
        "claim_adaptation",
        contract=Claim,
        kind="function",
        display_name="Claim adaptation (extracted ad -> validated Claims)",
    )
