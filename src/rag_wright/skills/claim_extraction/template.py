"""Schema ASSET for the `claim_extraction` agent skill (referenced by this folder's SKILL.md).

The docling-graph extraction template the skill fills: `ExtractedAd` (the subject ad) with its checkable
`ExtractedClaim`s. Loose strings by design (robust to model output); the deterministic `claim_adaptation`
FUNCTION maps them to the closed CC-1 `Claim` vocab. Co-located with the skill because the schema IS part of
the authored extraction method (an Agent-Skill asset), not a hidden implementation detail.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from rag_wright.capabilities.dg_extraction import edge


class ExtractedClaim(BaseModel):
    """One checkable assertion the LLM reads out of a subject ad (a docling-graph child entity)."""

    model_config = ConfigDict(graph_id_fields=["assertion_text"], extra="ignore", populate_by_name=True)

    assertion_text: str = Field(
        description="One checkable factual claim the ad makes, quoted or closely paraphrased (one claim per entry)")
    claim_type: str = Field(
        default="",
        description=("The kind of claim, chosen from: efficacy, comparative, pricing, health, environmental, "
                     "endorsement, performance, guarantee"))
    actor: str = Field(default="", description=(
        "DEON-8: the ROLE of the party this claim involves -- a role word, NOT a person's or company's name. "
        "Choose the general role: advertiser, endorser, expert, manufacturer, seller. (E.g. 'Dr. Miller "
        "recommends ...' -> endorser, not 'Dr. Miller'.) Empty if no clear actor."))
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
