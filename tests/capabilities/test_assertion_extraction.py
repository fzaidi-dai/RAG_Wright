"""SEG-3: the domain-neutral verbatim assertion extractor (subject text -> CheckableFact[]). Hermetic: the
docling-graph extraction act is injected (aextract_fn), so no LLM/model call."""

from __future__ import annotations

from rag_wright.capabilities.assertion_extraction import (
    ExtractedAssertion,
    ExtractedAssertions,
    aassertion_extraction,
    to_facts,
)
from rag_wright.contracts.compliance import Claim


def test_to_facts_adapts_verbatim_assertions_and_skips_blank():
    ex = ExtractedAssertions(subject="s", assertions=[
        ExtractedAssertion(assertion_text="The supplier's liability is capped at the fees paid."),
        ExtractedAssertion(assertion_text="   "),                      # blank -> skipped
        ExtractedAssertion(assertion_text="The product cures arthritis in two weeks."),
    ])
    facts = to_facts(ex, source_doc="doc")
    assert len(facts) == 2                                             # blank dropped
    assert facts[0].assertion_text == "The supplier's liability is capped at the fees paid."  # VERBATIM
    assert facts[0].fact_id != facts[1].fact_id                        # unique ids
    assert not isinstance(facts[0], Claim)                             # domain-neutral CheckableFact, NOT a Claim
    assert getattr(facts[0], "claim_type", None) is None              # no ad claim_type


async def test_aassertion_extraction_uses_the_injected_extractor():
    async def _stub(text, model, *, template, **kw):
        assert template is ExtractedAssertions
        return ExtractedAssertions(subject="s", assertions=[ExtractedAssertion(assertion_text=text.strip())])

    facts = await aassertion_extraction("A verbatim claim here.", model=object(), source_doc="d", aextract_fn=_stub)
    assert len(facts) == 1 and facts[0].assertion_text == "A verbatim claim here."


async def test_aassertion_extraction_empty_when_extraction_yields_none():
    async def _stub(text, model, *, template, **kw):
        return None

    assert await aassertion_extraction("x", model=object(), source_doc="d", aextract_fn=_stub) == []
