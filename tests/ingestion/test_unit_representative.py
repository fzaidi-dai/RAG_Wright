"""PS-R3: the `unit_representative` hook -- which member span represents a unit. Grouping (the `UnitGrouper`) decides
which spans form a unit; the representative decides which of them the unit is labelled and cited by: it becomes the
unit's `anchor` (the citation its records carry) and its primary tag leads the unit's `tags` (the label the extractor
reads). No hook: the grouper's own choice (the default grouper: the first member)."""
from __future__ import annotations

import asyncio

import pytest

from rag_wright.api import IngestionContractError, Span, TaggedSpan, UnitExtraction, build_ingestion
from rag_wright.ingestion.group import apply_unit_representative
from rag_wright.store.seam import KgNode
from tests.ingestion.test_builder import FIXTURES, _FakeEmbedder, _ws


def _ts(i, text, tag, kind=None):
    return TaggedSpan(span=Span(span_id=f"c#{i}", parent_chunk_id="c", span_index=i, text=text, start=0,
                                end=len(text), kind=kind), tags=[tag] if tag else [])


def _units_seen(tmp_path, **kw):
    seen = []

    async def extractor(unit, *, source_doc_id):
        seen.append((unit.anchor.span_id, [s.span_id for s in unit.spans], list(unit.tags)))
        return UnitExtraction(nodes=[KgNode("Record", "record_id", {
            "record_id": f"{source_doc_id}:{unit.index}", "span_id": unit.anchor.span_id, "confidence": "EXTRACTED"})])

    pipe = build_ingestion(extractor, embedder=_FakeEmbedder(), progress=lambda _l: None, **kw)
    asyncio.run(pipe.aingest(_ws(), [str(FIXTURES / "textile_spec_sheet.md")], cache_dir=tmp_path / "cache"))
    return seen


def test_without_the_hook_a_unit_is_represented_by_its_first_member(tmp_path):
    assert all(anchor == spans[0] for anchor, spans, _ in _units_seen(tmp_path))


def test_the_hook_chooses_the_anchor_the_records_cite(tmp_path):
    seen = _units_seen(tmp_path, unit_representative=lambda members: members[-1])
    assert all(anchor == spans[-1] for anchor, spans, _ in seen)
    assert any(len(spans) > 1 for _, spans, _ in seen)  # the fixture has multi-span units, so this is not vacuous


def test_the_representatives_primary_tag_leads_the_units_tags():
    heading, body = _ts(0, "Section 9. Uncapped Liability.", "Cap"), _ts(1, "Notwithstanding Section 8 ...", "Uncapped")
    from rag_wright.api import Unit

    unit = Unit(index=0, anchor=heading.span, spans=[heading.span, body.span], text="x", tags=["Cap", "Uncapped"])
    (out,) = apply_unit_representative([unit], [heading, body], lambda members: members[1])
    assert out.anchor.span_id == "c#1" and out.tags == ["Uncapped", "Cap"]


def test_a_representative_outside_the_unit_is_rejected():
    a, b, outsider = _ts(0, "a", "X"), _ts(1, "b", "Y"), _ts(2, "z", "Z")
    from rag_wright.api import Unit

    unit = Unit(index=0, anchor=a.span, spans=[a.span, b.span], text="x", tags=["X"])
    with pytest.raises(IngestionContractError):
        apply_unit_representative([unit], [a, b, outsider], lambda members: outsider)


# --- the reference contracts pack's rule: the operative span, not the heading ------------------------------------


def test_operative_span_skips_a_heading_label():
    from rag_wright.packs.contracts.subgraphs.contract_ingestion_pipeline import operative_span

    heading = _ts(0, "Section 9. Uncapped Liability.", "Cap On Liability")
    body = _ts(1, "Notwithstanding Section 8, there shall be no cap on liability for indemnification.",
               "Uncapped Liability")
    assert operative_span([heading, body]) is body


def test_operative_span_skips_heading_kinds_and_untagged_spans():
    from rag_wright.packs.contracts.subgraphs.contract_ingestion_pipeline import operative_span

    title = _ts(0, "Limitation of Liability and Indemnification Obligations of the Parties", "Cap On Liability",
                kind="heading")
    untagged = _ts(1, "As used in this Section, the following terms apply to the parties hereto.", "NONE")
    body = _ts(2, "Either party's aggregate liability shall not exceed the fees paid.", "Cap On Liability")
    assert operative_span([title, untagged, body]) is body


def test_operative_span_falls_back_to_a_tagged_heading_then_the_first_member():
    from rag_wright.packs.contracts.subgraphs.contract_ingestion_pipeline import operative_span

    heading = _ts(0, "Governing Law", "Governing Law")
    untagged = _ts(1, "See the schedule attached hereto for the details of this arrangement.", "NONE")
    assert operative_span([heading, untagged]) is heading  # no tagged operative span -> the tagged heading
    a, b = _ts(0, "Section 3.", "NONE"), _ts(1, "Text without a function at all, in a sentence.", "NONE")
    assert operative_span([a, b]) is a  # nothing tagged at all -> the first member (the grouper's own choice)


# --- the reference rule as measured (PS-R3, CUAD: rule D): vote over the operative (non-heading) members ---------


def _scored(i, text, scores, kind=None):
    ranked = sorted(scores, key=scores.get, reverse=True)
    return TaggedSpan(span=Span(span_id=f"c#{i}", parent_chunk_id="c", span_index=i, text=text, start=0,
                                end=len(text), kind=kind), tags=ranked, scores=scores)


def test_provision_vote_labels_by_summed_operative_probabilities_and_cites_the_strongest_member():
    from rag_wright.packs.contracts.subgraphs.contract_ingestion_pipeline import provision_vote

    heading = _scored(0, "Section 8. Limitation of Liability.", {"Cap On Liability": 0.6, "Uncapped Liability": 0.3})
    carve = _scored(1, "Except for breaches of confidentiality, in no event shall liability exceed the fees paid.",
                    {"Uncapped Liability": 0.55, "Cap On Liability": 0.40})
    cap = _scored(2, "In no event shall either party be liable for any indirect or consequential damages.",
                  {"Cap On Liability": 0.70, "Uncapped Liability": 0.20})
    rep = provision_vote([heading, carve, cap])
    assert rep.primary_tag == "Cap On Liability"  # 0.40 + 0.70 beats 0.55 + 0.20 (the heading does not vote)
    assert rep.span.span_id == "c#2"  # cited by the operative member most confident in that label


def test_provision_vote_keeps_a_carve_out_section_uncapped():
    from rag_wright.packs.contracts.subgraphs.contract_ingestion_pipeline import provision_vote

    heading = _scored(0, "Section 9. Uncapped Liability.", {"Cap On Liability": 0.6, "Uncapped Liability": 0.35})
    body = _scored(1, "Notwithstanding Section 8, there shall be no cap on liability for indemnification.",
                   {"Uncapped Liability": 0.6, "Cap On Liability": 0.3})
    rep = provision_vote([heading, body])
    assert (rep.primary_tag, rep.span.span_id) == ("Uncapped Liability", "c#1")


def test_provision_vote_without_probabilities_falls_back_to_the_operative_span():
    from rag_wright.packs.contracts.subgraphs.contract_ingestion_pipeline import operative_span, provision_vote

    heading = _ts(0, "Section 9. Uncapped Liability.", "Cap On Liability")
    body = _ts(1, "Notwithstanding Section 8, there shall be no cap on liability for indemnification.",
               "Uncapped Liability")
    assert provision_vote([heading, body]) is operative_span([heading, body])


def test_a_representative_may_relabel_a_member():
    a = _scored(0, "a", {"X": 0.9})
    b = _scored(1, "b", {"Y": 0.9})
    from rag_wright.api import Unit

    unit = Unit(index=0, anchor=a.span, spans=[a.span, b.span], text="x", tags=["X", "Y"])
    (out,) = apply_unit_representative([unit], [a, b], lambda members: members[1].model_copy(update={"primary": "Z"}))
    assert out.anchor.span_id == "c#1" and out.tags == ["Z", "X", "Y"]
