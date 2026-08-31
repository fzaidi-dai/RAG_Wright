"""SEG-3: the DOMAIN-NEUTRAL verbatim assertion extractor -- subject text -> `CheckableFact[]`.

The generic analog of `claim_extraction` (which is the ADVERTISING specialization): the SAME docling-graph
extraction act with a domain-neutral template (`ExtractedAssertions`, no `claim_type`), plus a deterministic
adaptation (`to_facts`). One LLM act reads the CHECKABLE ASSERTIONS out of a chunk of subject text, quoted
VERBATIM so each can be cited faithfully and mapped back to its source element (SEG-4). Used by the subject
compliance pipeline; `claim_extraction` remains the ad path (typed `Claim`s).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from rag_wright.capabilities.dg_extraction import aextract_parties, edge
from rag_wright.contracts.compliance import CheckableFact

__all__ = ["ExtractedAssertion", "ExtractedAssertions", "to_facts", "aassertion_extraction"]


class ExtractedAssertion(BaseModel):
    """One checkable assertion the LLM reads out of a subject document (a docling-graph child entity)."""

    model_config = ConfigDict(graph_id_fields=["assertion_text"], extra="ignore", populate_by_name=True)

    assertion_text: str = Field(
        description=("One checkable factual assertion / claim / statement the document makes, quoted VERBATIM "
                     "from the source text -- the exact words (one assertion per entry), so it can be cited "
                     "faithfully. Do NOT paraphrase, summarize, or merge multiple assertions."))
    actor: str = Field(
        default="",
        description=("DEON-5: the ROLE of the party this assertion involves -- a role word, NOT a person's or "
                     "company's name. Choose the general role: advertiser, endorser, expert, manufacturer, "
                     "seller, employer, or party. (E.g. 'Dr. Miller recommends ...' -> endorser, not 'Dr. "
                     "Miller'.) Lets a rule that binds a role be gated to documents where that role appears. "
                     "Empty if no clear actor."))


class ExtractedAssertions(BaseModel):
    """The subject document and the distinct checkable assertions it makes (the docling-graph root entity)."""

    model_config = ConfigDict(graph_id_fields=["subject"], extra="ignore", populate_by_name=True)

    subject: str = Field(description="A short label for the subject (a headline phrase or the document topic)")
    assertions: list[ExtractedAssertion] = edge(
        "MAKES_ASSERTION", default_factory=list,
        description="The distinct checkable assertions the document makes (one entry per assertion, verbatim)")


def to_facts(extracted: ExtractedAssertions, *, source_doc: str) -> list[CheckableFact]:
    """DETERMINISTIC (no model): adapt extracted assertions to `CheckableFact`s. Blank assertion skipped;
    `fact_id` = content-hash. Domain-neutral -- no `claim_type` (that is the `claim_extraction` specialization).
    The structural locator (section / ¶ / bullet) is attached later, in SEG-4. DEON-5: an extracted `actor` rides
    as a dimension-agnostic `Constraint("actor", ...)` on the fact's `scope`."""
    from rag_wright.contracts.compliance import Constraint

    out: list[CheckableFact] = []
    for index, item in enumerate(extracted.assertions):
        text = (item.assertion_text or "").strip()
        if not text:
            continue
        actor = (getattr(item, "actor", "") or "").strip().lower()
        scope = [Constraint(dimension="actor", value=actor)] if actor else []
        out.append(CheckableFact(
            fact_id=CheckableFact.make_id(source_doc, index, text), source_doc=source_doc, assertion_text=text,
            scope=scope))
    return out


async def aassertion_extraction(text: str, *, model: Any, source_doc: str,
                                aextract_fn: Any = aextract_parties) -> list[CheckableFact]:
    """Extract the checkable assertions (verbatim) from a chunk of subject text -> `CheckableFact`s: the
    docling-graph verbatim-binding extraction act fills `ExtractedAssertions`, then `to_facts` adapts.
    Returns [] if extraction yields nothing. `aextract_fn` is injected for hermetic tests."""
    extracted = await aextract_fn(text, model, template=ExtractedAssertions, extraction_contract="direct")
    if extracted is None:
        return []
    return to_facts(extracted, source_doc=source_doc)
