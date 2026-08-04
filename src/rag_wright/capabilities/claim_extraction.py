"""CC-3 (compliance §13 C-3): the `claim_extraction` capability.

Subject ad text -> `Claim[]` (the CC-1 contract) -- the SUBJECT side of the compliance check, mirroring CC-2's
`requirement_extraction` on the regulatory side. Reuses the docling-graph seam (`extract_parties`, generic over
`template`) with a Claim template, then adapts each extracted assertion to a validated `Claim`. Ads are short,
so extraction stays on the seam's default "direct" contract (unlike CC-2's long regulation sections).

The template is what the LLM fills (loose strings); the adapter maps to the closed CC-1 vocab -- an off-vocab
claim_type is KEPT (a real assertion is never dropped -- a missed claim is a missed potential violation) but
flagged AMBIGUOUS; a blank assertion is skipped. Each claim carries its span provenance (source_doc + text).
"""

from __future__ import annotations

from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

from rag_wright.capabilities.dg_extraction import edge, extract_parties
from rag_wright.contracts.compliance import Claim, ClaimType
from rag_wright.contracts.provenance import ConfidenceTag

_CLAIM_TYPES = {c.value for c in ClaimType}
# off-vocab fallback: keep the claim (never drop a checkable assertion) but mark it AMBIGUOUS. EFFICACY is the
# broadest "the product works" type; the AMBIGUOUS tag is what actually signals the uncertainty downstream.
_FALLBACK_CLAIM_TYPE = ClaimType.EFFICACY


class ExtractedClaim(BaseModel):
    """One checkable assertion the LLM reads out of a subject ad (a docling-graph child entity). Loose strings
    by design; the adapter maps them to the closed CC-1 vocab."""

    model_config = ConfigDict(graph_id_fields=["assertion_text"], extra="ignore", populate_by_name=True)

    assertion_text: str = Field(
        description="One checkable factual claim the ad makes, quoted or closely paraphrased (one claim per entry)")
    claim_type: str = Field(
        default="",
        description=("The kind of claim, chosen from: efficacy, comparative, pricing, health, environmental, "
                     "endorsement, performance, guarantee"))
    actor: str = Field(default="", description="Who makes or is featured in the claim (advertiser, endorser, expert)")
    subject_product: str = Field(default="", description="The product or brand the claim is about")
    quantitative_value: str = Field(
        default="", description="Any specific number/quantity claimed, e.g. '30 pounds in one month', '2x faster'")
    disclosures_present: list[str] = Field(
        default_factory=list,
        description="Disclaimers/qualifiers present near the claim, e.g. '#ad', 'paid partnership', 'results vary'")
    evidence_referenced: bool = Field(
        default=False, description="Whether the ad references evidence/substantiation for the claim (a study, data)")
    medium: str = Field(default="", description="The medium, e.g. social, tv, print, podcast, web")


class ExtractedAd(BaseModel):
    """The subject document and the distinct checkable claims it makes (the docling-graph root entity)."""

    model_config = ConfigDict(graph_id_fields=["subject"], extra="ignore", populate_by_name=True)

    subject: str = Field(description="A short label for the subject ad (the brand/product or a headline phrase)")
    claims: list[ExtractedClaim] = edge(
        "MAKES_CLAIM", default_factory=list,
        description="The distinct checkable claims the ad makes (one entry per claim)")


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
    """Adapt an extracted ad to validated `Claim`s. Off-vocab claim_type -> fallback + AMBIGUOUS; blank
    assertion skipped. `claim_id` uses the content-hash scheme; the (source_doc, assertion) is the span cite."""
    out: list[Claim] = []
    for index, item in enumerate(extracted.claims):
        text = (item.assertion_text or "").strip()
        if not text:
            continue
        claim_type, ambiguous = _coerce_claim_type(item.claim_type)
        out.append(Claim(
            claim_id=Claim.make_id(source_doc, index, text),
            source_doc=source_doc,
            claim_type=claim_type,
            assertion_text=text,
            actor=_clean(item.actor),
            subject_product=_clean(item.subject_product),
            quantitative_value=_clean(item.quantitative_value),
            disclosures_present=[d.strip() for d in item.disclosures_present if d and d.strip()],
            evidence_referenced=bool(item.evidence_referenced),
            medium=_clean(item.medium),
            confidence=ConfidenceTag.AMBIGUOUS if ambiguous else ConfidenceTag.EXTRACTED,
        ))
    return out


ExtractFn = Callable[..., Any]  # (text, model, *, template, **kw) -> ExtractedAd | None


def claim_extraction(
    text: str, *, model: Any, source_doc: str,
    extract_fn: ExtractFn = extract_parties, max_tokens: int = 1500, preamble_chars: int = 8000,
    extraction_contract: str = "direct",
) -> list[Claim]:
    """Extract the checkable `Claim`s a subject ad makes. `extract_fn` is the docling-graph seam (default
    `extract_parties`, generic over template); injected in tests. Ads are short, so `extraction_contract`
    defaults to "direct" (a single call fits). Returns [] if extraction yields nothing."""
    extracted = extract_fn(text, model, template=ExtractedAd,
                           max_tokens=max_tokens, preamble_chars=preamble_chars,
                           extraction_contract=extraction_contract)
    if extracted is None:
        return []
    return to_claims(extracted, source_doc=source_doc)


def register_claim_extraction(registry) -> None:
    """Register `claim_extraction` (function; CC-3, compliance §13). Contract = `Claim`."""
    registry.register(
        "claim_extraction",
        contract=Claim,
        kind="function",
        display_name="Claim extraction (subject ad -> checkable claims)",
    )
