"""CC-3 (compliance §13 C-3): the `claim_extraction` capability.

Subject ad text -> `Claim[]` via the docling-graph seam (reused `extract_parties` with a Claim template) +
an adapter to the CC-1 contract. Hermetic: the docling-graph run is stubbed (`extract_fn`), no LLM. Ads are
short, so extraction stays on the seam's default "direct" contract (unlike the long regulation sections in
CC-2, which use "auto"). An off-vocab claim_type is kept but marked AMBIGUOUS; a blank assertion is skipped.
"""

from __future__ import annotations

from rag_wright.capabilities.claim_extraction import (
    ExtractedAd,
    ExtractedClaim,
    claim_extraction,
    register_claim_adaptation,
    register_claim_extraction,
    to_claims,
)
from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.contracts.compliance import Claim, ClaimType
from rag_wright.contracts.provenance import ConfidenceTag


def _ad(*claims: ExtractedClaim, subject: str = "influencer_ad") -> ExtractedAd:
    return ExtractedAd(subject=subject, claims=list(claims))


# --- adapter: extracted template -> Claim contract -----------------------------------------------


def test_adapter_maps_fields_id_and_provenance():
    extracted = _ad(
        ExtractedClaim(
            assertion_text="clinically proven to erase deep wrinkles in 7 days",
            claim_type="health", actor="influencer", subject_product="LumaGlow",
            quantitative_value="7 days", disclosures_present=[], evidence_referenced=False, medium="social",
        )
    )
    claims = to_claims(extracted, source_doc="influencer_skincare_no_disclosure")
    assert len(claims) == 1
    c = claims[0]
    assert isinstance(c, Claim)
    assert c.claim_type is ClaimType.HEALTH and c.medium == "social"
    assert c.assertion_text.startswith("clinically proven")
    assert c.fact_id == Claim.make_id("influencer_skincare_no_disclosure", 0, c.assertion_text)
    assert c.evidence_referenced is False and c.confidence is ConfidenceTag.EXTRACTED


def test_disclosures_carry_through_and_mark_evidence():
    extracted = _ad(
        ExtractedClaim(assertion_text="two shades whiter in four weeks", claim_type="efficacy",
                       disclosures_present=["#ad", "individual results vary"], evidence_referenced=True))
    c = to_claims(extracted, source_doc="compliant_disclosed_ad")[0]
    assert c.disclosures_present == ["#ad", "individual results vary"] and c.evidence_referenced is True


def test_offvocab_claim_type_is_kept_but_ambiguous():
    c = to_claims(_ad(ExtractedClaim(assertion_text="vibes are immaculate", claim_type="vibes")),
                  source_doc="ad")[0]
    assert c.claim_type in set(ClaimType)  # coerced to a valid member, never invalid
    assert c.confidence is ConfidenceTag.AMBIGUOUS  # flagged so downstream treats it cautiously


def test_blank_assertion_is_skipped():
    claims = to_claims(
        _ad(ExtractedClaim(assertion_text="   ", claim_type="health"),
            ExtractedClaim(assertion_text="melts fat right off", claim_type="efficacy")),
        source_doc="ad")
    assert [c.assertion_text for c in claims] == ["melts fat right off"]


def test_distinct_ids_for_distinct_assertions():
    claims = to_claims(
        _ad(ExtractedClaim(assertion_text="lose 30 pounds", claim_type="efficacy"),
            ExtractedClaim(assertion_text="guaranteed results", claim_type="guarantee")),
        source_doc="ad")
    assert len({c.fact_id for c in claims}) == 2


# --- the capability: text -> Claim[] (docling-graph stubbed) -------------------------------------


def test_extraction_uses_the_seam_with_direct_contract():
    captured = {}

    def fake_extract(text, model, *, template, **kw):
        captured["template"] = template
        captured["extraction_contract"] = kw.get("extraction_contract")
        return _ad(ExtractedClaim(assertion_text="erase wrinkles in 7 days", claim_type="health"))

    claims = claim_extraction("…ad…", model=None, source_doc="influencer_ad", extract_fn=fake_extract)
    assert captured["template"] is ExtractedAd
    assert captured["extraction_contract"] == "direct"  # ads are short -> direct (not the CC-2 "auto")
    assert [c.assertion_text for c in claims] == ["erase wrinkles in 7 days"]


def test_extraction_none_yields_empty_list():
    assert claim_extraction("x", model=None, source_doc="ad", extract_fn=lambda *a, **k: None) == []


# --- ASYNC-C1 (ADR-0057): aclaim_extraction -- the async twin (await extract ACT, then to_claims) --------------


async def test_aclaim_extraction_awaits_the_async_seam_then_adapts():
    from rag_wright.capabilities.claim_extraction import aclaim_extraction

    captured = {}

    async def afake_extract(text, model, *, template, **kw):
        captured["template"] = template
        captured["extraction_contract"] = kw.get("extraction_contract")
        return _ad(ExtractedClaim(assertion_text="erase wrinkles in 7 days", claim_type="health"))

    claims = await aclaim_extraction("…ad…", model=None, source_doc="influencer_ad", aextract_fn=afake_extract)
    assert captured["template"] is ExtractedAd
    assert captured["extraction_contract"] == "direct"  # ads are short -> direct
    assert [c.assertion_text for c in claims] == ["erase wrinkles in 7 days"]


async def test_aclaim_extraction_none_yields_empty_list():
    from rag_wright.capabilities.claim_extraction import aclaim_extraction

    async def _none(*a, **k):
        return None

    assert await aclaim_extraction("x", model=None, source_doc="ad", aextract_fn=_none) == []


# --- registration --------------------------------------------------------------------------------


def test_registers_the_skill_and_the_function_split():
    # SKILL-SPLIT: the LLM extraction is an agent_skill; the deterministic adaptation is a function
    reg = CapabilityRegistry()
    register_claim_extraction(reg)
    register_claim_adaptation(reg)
    skill = reg.get("claim_extraction")
    assert skill.kind == "agent_skill" and skill.contract is ExtractedAd
    fn = reg.get("claim_adaptation")
    assert fn.kind == "function" and fn.contract is Claim


def test_skill_folder_has_the_method_and_the_schema_asset():
    # a Skill is a folder: SKILL.md (the method) + the co-located schema asset (template.py)
    from pathlib import Path

    import rag_wright.skills.claim_extraction as skill_pkg
    folder = Path(skill_pkg.__file__).parent
    assert (folder / "SKILL.md").exists() and (folder / "template.py").exists()
    method = (folder / "SKILL.md").read_text(encoding="utf-8")
    assert "template.py" in method and "checkable" in method.lower()
