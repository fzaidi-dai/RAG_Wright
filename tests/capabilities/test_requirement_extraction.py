"""CC-2 (compliance §13 C-1/C-2): the `requirement_extraction` capability.

Regulatory section text -> `Requirement[]` via the docling-graph seam (reused `extract_parties` with a new
Requirement template) + an adapter to the CC-1 contract. Hermetic: the docling-graph run is stubbed
(`extract_fn`), so no LLM. Deontic force + applicability (claim_type constraints) are mapped to the closed
vocab; an off-vocab deontic downgrades to AMBIGUOUS; an off-vocab claim_type is dropped.
"""

from __future__ import annotations

from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.capabilities.requirement_extraction import (
    ExtractedRegulationSection,
    ExtractedRequirement,
    register_requirement_extraction,
    requirement_extraction,
    to_requirements,
)
from rag_wright.contracts.compliance import DeonticType, Requirement
from rag_wright.contracts.provenance import ConfidenceTag


def _section(*reqs: ExtractedRequirement, section: str = "255.5") -> ExtractedRegulationSection:
    return ExtractedRegulationSection(section=section, requirements=list(reqs))


# --- adapter: extracted template -> Requirement contract -----------------------------------------


def test_adapter_maps_fields_id_citation_and_applicability():
    extracted = _section(
        ExtractedRequirement(
            requirement_text="A material connection must be disclosed.",
            deontic_type="obligation", actor="advertiser",
            claim_types=["endorsement", "health"], evidence_standard="",
        )
    )
    reqs = to_requirements(extracted, source="FTC 16 CFR 255", section="255.5")
    assert len(reqs) == 1
    r = reqs[0]
    assert isinstance(r, Requirement)
    assert r.deontic_type is DeonticType.OBLIGATION and r.actor == "advertiser"
    assert r.citation == "§ 255.5"
    assert r.requirement_id == Requirement.make_id("FTC 16 CFR 255", "255.5", r.requirement_text)
    assert {c.as_tuple() for c in r.applicability_scope} == {("claim_type", "endorsement"), ("claim_type", "health")}
    assert r.confidence is ConfidenceTag.EXTRACTED


def test_offvocab_deontic_downgrades_to_ambiguous():
    reqs = to_requirements(
        _section(ExtractedRequirement(requirement_text="X must be substantiated.", deontic_type="mandate")),
        source="reg", section="255.1")
    assert reqs[0].confidence is ConfidenceTag.AMBIGUOUS
    assert reqs[0].deontic_type in set(DeonticType)  # coerced to a valid member, never invalid


def test_offvocab_claim_type_is_dropped_not_fabricated():
    reqs = to_requirements(
        _section(ExtractedRequirement(requirement_text="Y.", deontic_type="prohibition",
                                      claim_types=["health", "vibes"])),
        source="reg", section="255.2")
    assert {c.value for c in reqs[0].applicability_scope} == {"health"}  # 'vibes' dropped


def test_blank_requirement_text_is_skipped():
    reqs = to_requirements(
        _section(ExtractedRequirement(requirement_text="   ", deontic_type="obligation"),
                 ExtractedRequirement(requirement_text="Real rule.", deontic_type="permission")),
        source="reg", section="255.3")
    assert [r.requirement_text for r in reqs] == ["Real rule."]


def test_missing_actor_defaults_unspecified():
    reqs = to_requirements(
        _section(ExtractedRequirement(requirement_text="Z.", deontic_type="obligation", actor="")),
        source="reg", section="255.4")
    assert reqs[0].actor == "unspecified"


# --- the capability: text -> Requirement[] (docling-graph stubbed) -------------------------------


def test_extraction_uses_the_seam_and_returns_requirements():
    captured = {}

    def fake_extract(text, model, *, template, **kw):
        captured["template"] = template
        captured["text"] = text
        captured["extraction_contract"] = kw.get("extraction_contract")
        return _section(ExtractedRequirement(requirement_text="Disclose connections.", deontic_type="obligation"))

    reqs = requirement_extraction(
        "…material connection…", model=None, source="FTC 16 CFR 255", section="255.5", extract_fn=fake_extract)
    assert captured["template"] is ExtractedRegulationSection
    # long regulatory sections must NOT use the seam's contract-tuned "direct" (it silently under-extracts)
    assert captured["extraction_contract"] == "auto"
    assert [r.requirement_text for r in reqs] == ["Disclose connections."]
    assert reqs[0].citation == "§ 255.5"


def test_extraction_none_yields_empty_list():
    reqs = requirement_extraction("x", model=None, source="r", section="255.0",
                                  extract_fn=lambda *a, **k: None)
    assert reqs == []


# --- registration (twofold: slug + this fn; manifest in manifests.py) ----------------------------


def test_registers_as_a_function():
    reg = CapabilityRegistry()
    register_requirement_extraction(reg)
    entry = reg.get("requirement_extraction")
    assert entry.kind == "function" and entry.contract is Requirement
