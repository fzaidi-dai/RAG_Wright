"""CC-2 (compliance §13), SKILL-SPLIT: the `requirement_extraction` SUBGRAPH.

A hardened LangGraph on scaffold.py: START -> extract[retry] -> adapt -> END. `requirement_extraction` is a
subgraph (not a single-shot skill) because its docling-graph `auto/dense` extraction is multi-LLM-call and the
extract -> adapt chaining is a deterministic workflow. Hermetic: extract/adapt DI'd, no LLM.

Covers: the wired extract->adapt happy path, dead-letter on extraction exhaustion, the None-extraction ->
empty-list path, `run_requirement_extraction` invoke convenience, and registration as a subgraph.
"""

from __future__ import annotations

from rag_wright.capabilities.requirement_extraction import (
    ExtractedRegulationSection,
    ExtractedRequirement,
)
from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.contracts.compliance import DeonticType, Requirement
from rag_wright.subgraphs.requirement_extraction import (
    build_requirement_extraction,
    register_requirement_extraction,
    run_requirement_extraction,
)


def _adapt(extracted, source, section):
    # a trivial deterministic adapter stand-in: turn each raw string into a Requirement
    return [
        Requirement(
            requirement_id=Requirement.make_id(source, section, text),
            source=source, citation=f"§ {section}", deontic_type=DeonticType.OBLIGATION,
            actor="advertiser", applicability_scope=[], requirement_text=text,
        )
        for text in extracted
    ]


# --- the subgraph: extract -> adapt, hardened ----------------------------------------------------


async def test_extract_then_adapt_happy_path():
    captured = {}

    async def extract_fn(text):
        captured["text"] = text
        return ["Disclose connections."]  # raw extraction stand-in

    graph = build_requirement_extraction(extract_fn, _adapt)
    out = await graph.ainvoke({"text": "…material connection…", "source": "FTC 16 CFR 255", "section": "255.5"})
    assert captured["text"] == "…material connection…"
    assert out.get("dead_letter") is None
    reqs = out["requirements"]
    assert [r.requirement_text for r in reqs] == ["Disclose connections."]
    assert reqs[0].citation == "§ 255.5"


async def test_extract_failure_dead_letters_and_yields_empty():
    async def boom(text):
        raise RuntimeError("granite down")

    graph = build_requirement_extraction(boom, _adapt)
    out = await graph.ainvoke({"text": "x", "source": "reg", "section": "255.1"})
    assert out.get("dead_letter") and out["dead_letter"]["stage"] == "extract"
    assert out["requirements"] == []  # adapt handles the dead-letter case -> []


async def test_none_extraction_yields_empty_without_dead_letter():
    async def _none(text):
        return None

    graph = build_requirement_extraction(_none, _adapt)
    out = await graph.ainvoke({"text": "x", "source": "reg", "section": "255.0"})
    assert out.get("dead_letter") is None
    assert out["requirements"] == []  # None extraction -> [] (not an error)


# --- run_requirement_extraction: single-section invoke convenience (used by CC-5) ----------------


async def test_run_invokes_the_subgraph_for_one_section():
    # extract_override is a stub returning the raw ExtractedRegulationSection; adapt = the real
    # to_requirements FUNCTION, so this exercises the real deterministic adaptation end to end.
    stub = ExtractedRegulationSection(
        section="255.5",
        requirements=[ExtractedRequirement(requirement_text="Disclose connections.", deontic_type="obligation")],
    )
    async def _stub(text):
        return stub

    reqs = await run_requirement_extraction(
        "…material connection…", model=None, source="FTC 16 CFR 255", section="255.5",
        extract_override=_stub)
    assert [r.requirement_text for r in reqs] == ["Disclose connections."]
    assert reqs[0].deontic_type is DeonticType.OBLIGATION
    assert reqs[0].citation == "§ 255.5"
    assert reqs[0].pages == [] and reqs[0].bbox is None  # no page provenance passed -> honestly absent (back-compat)


async def test_run_stamps_section_page_provenance_onto_each_requirement():
    # issue 0043: the section's pages/bbox (from the parse) are stamped on every extracted Requirement, so a
    # finding can point into the policy. pages/bbox are NOT part of the id (idempotency unaffected).
    stub = ExtractedRegulationSection(
        section="10.5", requirements=[
            ExtractedRequirement(requirement_text="Retain records six years.", deontic_type="obligation"),
            ExtractedRequirement(requirement_text="Provide access on request.", deontic_type="obligation")])

    async def _stub(text):
        return stub

    reqs = await run_requirement_extraction(
        "…records…", model=None, source="ClientPolicy", section="10.5", extract_override=_stub,
        pages=[7, 8], bbox=(1.0, 2.0, 3.0, 4.0))
    assert len(reqs) == 2
    assert all(r.pages == [7, 8] and r.bbox == (1.0, 2.0, 3.0, 4.0) for r in reqs)  # both carry the section's prov
    # id is content-derived, unchanged by pages
    from rag_wright.contracts.compliance import Requirement
    assert reqs[0].requirement_id == Requirement.make_id("ClientPolicy", "10.5", "Retain records six years.")


# --- registration: requirement_extraction is a SUBGRAPH ------------------------------------------


def test_registers_as_a_subgraph():
    reg = CapabilityRegistry()
    register_requirement_extraction(reg)
    entry = reg.get("requirement_extraction")
    assert entry.kind == "subgraph" and entry.contract is Requirement
