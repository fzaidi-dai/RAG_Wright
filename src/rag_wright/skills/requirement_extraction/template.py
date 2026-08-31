"""Schema ASSET for the `requirement_extraction` subgraph's extraction node (referenced by this folder's SKILL.md).

The docling-graph extraction template the extraction node fills: `ExtractedRegulationSection` (a § section) with
its `ExtractedRequirement`s. Loose strings by design (robust to model output); the deterministic
`requirement_adaptation` FUNCTION maps them to the closed CC-1 `Requirement` vocab. Co-located with the skill
because the schema IS part of the authored extraction method (an Agent-Skill asset).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from rag_wright.capabilities.dg_extraction import edge


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
    applicability: list[str] = Field(
        default_factory=list,
        description=("P3a (Gap 2): the conditions under which THIS rule applies, as 'dimension: value' pairs (one "
                     "per entry) -- for ANY policy domain, not only advertising. E.g. 'jurisdiction: California', "
                     "'employee_class: hourly', 'data_category: biometric', 'product_category: supplement'. Leave "
                     "empty if the rule applies unconditionally. (Advertising claim types go in claim_types.)"))
    evidence_standard: str = Field(
        default="", description="The substantiation the rule requires, if any (e.g. competent and reliable scientific evidence)")


class ExtractedRegulationSection(BaseModel):
    """A regulatory section and the distinct rules it states (the docling-graph root entity)."""

    model_config = ConfigDict(graph_id_fields=["section"], extra="ignore", populate_by_name=True)

    section: str = Field(description="The section number, e.g. 255.5")
    requirements: list[ExtractedRequirement] = edge(
        "STATES_REQUIREMENT", default_factory=list,
        description="The distinct rules stated in this section (one entry per rule)")
