"""CC-2 (compliance §13 C-1/C-2), SKILL-SPLIT: the extraction ACT + the `requirement_adaptation` FUNCTION.

`requirement_extraction` is a SUBGRAPH (its `auto/dense` extraction is multi-LLM-call; extract -> adapt is a
deterministic workflow) -- the subgraph itself is tested in `tests/subgraphs/test_requirement_extraction.py`.
This file covers the subgraph's two co-located pieces that live in `capabilities/requirement_extraction.py`:

- the extraction ACT (`extract_regulation_section`) -- the docling-graph seam is stubbed (`extract_fn`), so no LLM;
- the `requirement_adaptation` FUNCTION (`to_requirements`) -- deterministic vocab coercion / off-vocab handling.

Deontic force + applicability (claim_type constraints) are mapped to the closed vocab; an off-vocab deontic
downgrades to AMBIGUOUS; an off-vocab claim_type is dropped.
"""

from __future__ import annotations

from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.capabilities.requirement_extraction import (
    ExtractedRegulationSection,
    ExtractedRequirement,
    extract_regulation_section,
    register_requirement_adaptation,
    to_requirements,
)
from rag_wright.contracts.compliance import DeonticType, Requirement
from rag_wright.contracts.provenance import ConfidenceTag


def _section(*reqs: ExtractedRequirement, section: str = "255.5") -> ExtractedRegulationSection:
    return ExtractedRegulationSection(section=section, requirements=list(reqs))


# --- requirement_adaptation FUNCTION: extracted template -> Requirement contract ------------------


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


# --- the extraction ACT: fills the skill template via the docling-graph seam (stubbed) ------------


def test_extraction_act_uses_the_seam_with_the_template_and_auto_contract():
    captured = {}

    def fake_extract(text, model, *, template, **kw):
        captured["template"] = template
        captured["text"] = text
        captured["extraction_contract"] = kw.get("extraction_contract")
        return _section(ExtractedRequirement(requirement_text="Disclose connections.", deontic_type="obligation"))

    extracted = extract_regulation_section("…material connection…", model=None, extract_fn=fake_extract)
    assert captured["template"] is ExtractedRegulationSection
    # long regulatory sections must NOT use the seam's contract-tuned "direct" (it silently under-extracts)
    assert captured["extraction_contract"] == "auto"
    assert [r.requirement_text for r in extracted.requirements] == ["Disclose connections."]


def test_extraction_act_passes_none_through():
    assert extract_regulation_section("x", model=None, extract_fn=lambda *a, **k: None) is None


# --- registration: requirement_adaptation is a FUNCTION (deterministic) ---------------------------


def test_registers_requirement_adaptation_as_a_function():
    reg = CapabilityRegistry()
    register_requirement_adaptation(reg)
    entry = reg.get("requirement_adaptation")
    assert entry.kind == "function" and entry.contract is Requirement
