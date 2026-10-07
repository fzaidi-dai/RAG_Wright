"""CC-2 (compliance §13 C-1/C-2), SKILL-SPLIT: the extraction ACT + the `requirement_adaptation` FUNCTION.

`requirement_extraction` is a SUBGRAPH (its `auto/dense` extraction is multi-LLM-call; extract -> adapt is a
deterministic workflow) -- the subgraph itself is tested in `tests/subgraphs/test_requirement_extraction.py`.
This file covers the subgraph's two co-located pieces that live in `packs/compliance/capabilities/requirement_extraction.py`:

- the extraction ACT (`extract_regulation_section`) -- the docling-graph seam is stubbed (`extract_fn`), so no LLM;
- the `requirement_adaptation` FUNCTION (`to_requirements`) -- deterministic vocab coercion / off-vocab handling.

Deontic force + applicability (claim_type constraints) are mapped to the closed vocab; an off-vocab deontic
downgrades to AMBIGUOUS; an off-vocab claim_type is dropped.
"""

from __future__ import annotations

from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.packs.compliance.capabilities.requirement_extraction import (
    ExtractedRegulationSection,
    ExtractedRequirement,
    extract_regulation_section,
    operative_rule_spans,
    register_requirement_adaptation,
    to_requirements,
)
from rag_wright.packs.compliance.schemas.compliance import DeonticType, Requirement
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


def test_generic_applicability_conditions_become_constraints():
    # P3a (Gap 2): a CUSTOMER policy's applicability conditions -- ANY dimension, not just claim_type -- survive
    # into the KG as Constraints, recall-first (an unknown dimension is NOT dropped; the query side matches it).
    reqs = to_requirements(
        _section(ExtractedRequirement(
            requirement_text="Hourly employees must receive annual safety training.",
            deontic_type="obligation", actor="employer",
            applicability=["jurisdiction: California", "employee_class: Hourly"])),
        source="Cal Labor Code", section="6401")
    scope = {c.as_tuple() for c in reqs[0].applicability_scope}
    assert ("jurisdiction", "california") in scope
    assert ("employee_class", "hourly") in scope   # unknown dimension KEPT (recall-first, not dropped)


def test_applicability_and_claim_types_coexist_and_malformed_is_skipped():
    reqs = to_requirements(
        _section(ExtractedRequirement(
            requirement_text="Pricing claims in California must state the base price.",
            deontic_type="prohibition", claim_types=["pricing"],
            applicability=["jurisdiction: california", "no-colon", "  :  ", "product: supplement",
                           "claim_type: pricing"])),
        source="reg", section="1")
    scope = {c.as_tuple() for c in reqs[0].applicability_scope}
    assert ("claim_type", "pricing") in scope            # FTC claim_type path unchanged (+ not double-added)
    assert ("jurisdiction", "california") in scope        # generic dimension
    assert ("product", "supplement") in scope
    assert not any(dim in ("", "no-colon") for dim, _ in scope)  # malformed skipped
    assert sum(1 for d, v in scope if (d, v) == ("claim_type", "pricing")) == 1  # deduped across both fields


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


# --- CIC (ADR-0119 scaffolding): deterministic rule-span identification (split + operative gate + cue-type) ---


def test_operative_rule_spans_splits_gates_and_types():
    text = ("Advertisers must disclose any material connection with an endorser. "
            "Definitions in this part apply throughout. "
            "An endorser may not misrepresent their actual experience.")
    spans = operative_rule_spans(text)
    types = {t: d for t, d in spans}
    assert len(spans) == 2  # the definitional sentence (no deontic cue) is dropped
    assert types[next(t for t in types if "must disclose" in t)] == "obligation"
    assert types[next(t for t in types if "may not misrepresent" in t)] == "prohibition"
    assert not any("Definitions in this part" in t for t in types)


def test_operative_rule_spans_text_is_verbatim_not_paraphrased():
    text = "The seller must substantiate every efficacy claim with competent and reliable scientific evidence."
    spans = operative_rule_spans(text)
    assert len(spans) == 1 and spans[0][1] == "obligation" and spans[0][0] in text  # VERBATIM slice


def test_operative_rule_spans_empty_and_cueless():
    assert operative_rule_spans("") == []
    assert operative_rule_spans("This part describes definitions and scope.") == []  # no deontic cue -> nothing


# --- CIC (ADR-0119): the Jev-decision extraction act (opt-in backend) ---
import os  # noqa: E402

import pytest  # noqa: E402

from rag_wright.packs.compliance.capabilities.requirement_extraction import ajev_extract_regulation_section  # noqa: E402

_CT = ["efficacy", "comparative", "pricing", "health", "environmental", "endorsement", "performance", "guarantee"]


async def test_ajev_extract_gates_fills_and_is_verbatim(monkeypatch):
    import rag_wright.capabilities.invoke as inv

    async def fake_jev(resources, inputs):
        state = inputs["state"]
        is_rule = ("must disclose" in state) or ("may not" in state)  # the Jev operative gate
        ans = {"operative": {"noul": 0.9 if is_rule else 0.1}, "actor": {"choice": "advertiser"}}
        for ct in _CT:
            ans[f"ct_{ct}"] = {"noul": 0.8 if (ct == "endorsement" and "endorser" in state) else 0.1}
        return {"answers": ans}

    monkeypatch.setattr(inv, "capability_impl", lambda name: fake_jev)
    text = ("Advertisers must disclose any material connection with an endorser. "
            "Definitions in this part apply throughout. "
            "An endorser may not misrepresent their actual experience.")
    sec = await ajev_extract_regulation_section(text)
    assert len(sec.requirements) == 2  # definition dropped (no cue); both operative spans pass the Jev gate
    texts = [r.requirement_text for r in sec.requirements]
    assert all(t in text for t in texts)  # VERBATIM
    assert {r.deontic_type for r in sec.requirements} == {"obligation", "prohibition"}  # cue-rule
    assert all(r.actor == "advertiser" for r in sec.requirements)
    assert all("endorsement" in r.claim_types for r in sec.requirements)
    assert all(r.applicability == [] for r in sec.requirements)  # open fields empty in this path (documented)


async def test_ajev_extract_drops_spans_the_jev_gate_rejects(monkeypatch):
    import rag_wright.capabilities.invoke as inv

    async def all_nonrule(resources, inputs):
        ans = {"operative": {"noul": 0.1}, "actor": {"choice": "other"}}
        for ct in _CT:
            ans[f"ct_{ct}"] = {"noul": 0.0}
        return {"answers": ans}

    monkeypatch.setattr(inv, "capability_impl", lambda name: all_nonrule)
    sec = await ajev_extract_regulation_section("The seller must substantiate every efficacy claim.")
    assert sec.requirements == []  # cue present but Jev gate says not-a-rule -> dropped


@pytest.mark.model
@pytest.mark.skipif(not os.getenv("OPENROUTER_API_KEY"), reason="OPENROUTER_API_KEY not set")
def test_ajev_extract_live():
    import asyncio
    text = ("Endorsements must reflect the honest opinions of the endorser. "
            "For purposes of this part, an endorsement means any advertising message. "
            "An advertiser may not misrepresent a consumer's experience.")
    sec = asyncio.run(ajev_extract_regulation_section(text))
    assert len(sec.requirements) >= 2  # the two operative sentences; the definition is dropped
    assert all(r.requirement_text in text and r.deontic_type for r in sec.requirements)


async def test_ajev_gated_residual_open_fields(monkeypatch):
    # ADR-0119: the residual open-field extraction runs ONLY for rules with a conditional/evidence cue.
    import rag_wright.capabilities.invoke as inv
    import rag_wright.packs.compliance.capabilities.requirement_extraction as rex
    calls = []

    async def keep(resources, inputs):
        ans = {"operative": {"noul": 0.9}, "actor": {"choice": "employer"}}
        for ct in _CT:
            ans[f"ct_{ct}"] = {"noul": 0.0}
        return {"answers": ans}

    async def fake_residual(span, mid):
        calls.append(span)
        return (["jurisdiction: california"], "competent and reliable scientific evidence")

    monkeypatch.setattr(inv, "capability_impl", lambda name: keep)
    monkeypatch.setattr(rex, "_residual_open_fields", fake_residual)
    text = ("Employers must keep the log if the company has more than ten employees. "
            "Each injury must be recorded on the OSHA 300 Log.")
    sec = await ajev_extract_regulation_section(text)
    assert len(sec.requirements) == 2
    cued = next(r for r in sec.requirements if "if the company" in r.requirement_text)
    plain = next(r for r in sec.requirements if "OSHA 300 Log" in r.requirement_text)
    assert cued.applicability == ["jurisdiction: california"] and cued.evidence_standard.startswith("competent")
    assert plain.applicability == [] and plain.evidence_standard == ""
    assert len(calls) == 1  # residual invoked only for the cued rule
