"""ING-3 (ADR-0124): the engine's default, domain-neutral unit grouper (`group_units`).

A heading starts a unit and its content belongs to it; a table (with the heading above it) is one unit; page
furniture is dropped; a chunk change ends a unit; units are capped in size; an optional decider adjudicates
heading-like lines the parse did not label."""
from __future__ import annotations

import asyncio
import re

import pytest

from rag_wright.api import Span, TaggedSpan, check_units
from rag_wright.ingestion import group_units, segment_layout

from tests.ingestion.test_layout_segmenter import _segment_doc


def _norm(s: str) -> str:
    return re.sub(r"-{3,}", "---", " ".join(s.split()))


def _group(spans, **kw):
    return asyncio.run(group_units([TaggedSpan(span=s) for s in spans], **kw))


GOLD = {
    "textile_spec_sheet.md": [
        ("Fabric Specification Sheet Spec No.: FS-2041-B", 3),
        ("Construction Fabric structure: single jersey, 1x1 lycra plated.", 3),
        ("| Parameter | Proposed | Actual | |---|---|---|", 5),
        ("Composition 95% cotton (combed, 40s Ne)", 3),
        ("Finishing route Heat-setting at 190 °C, speed 18 m/min.", 4),
    ],
    "textile_test_report.md": [
        ("Test Report TR-0398 Sample: knitted T-shirt, style 107-208, colour navy.", 2),
        ("Summary The sample meets the dimensional stability requirement.", 3),
        ("Results | Test | Method | Result | Requirement | Pass/Fail | |---|---|---|---|---|", 4),
        ("Recommendations Re-dye with a reactive dye recipe and re-test to ISO 105-C06.", 3),
    ],
}


@pytest.mark.parametrize("name", list(GOLD))
def test_matches_the_hand_gold(name):
    spans = _segment_doc(name)
    units = _group(spans)
    check_units(spans, units)
    assert [(_norm(u.anchor.text), len(u.spans)) for u in units] == GOLD[name]


def _span(i: int, text: str, kind=None, chunk="c:0:h") -> Span:
    return Span(span_id=f"{chunk}#{i}", parent_chunk_id=chunk, span_index=i, start=0, end=len(text), text=text,
                kind=kind)


def test_page_furniture_and_empty_spans_are_dropped():
    spans = [_span(0, "Page 3 of 9", "page_header"), _span(1, "Yarn count: 40s Ne.", "paragraph"),
             _span(2, "-", "paragraph"), _span(3, "Confidential", "page_footer")]
    (unit,) = _group(spans)
    assert [s.span_id for s in unit.spans] == ["c:0:h#1"]


def test_a_chunk_change_ends_a_unit():
    spans = [_span(0, "Twist: Z.", "paragraph", chunk="c:0:h"), _span(0, "Twist: S.", "paragraph", chunk="c:1:h")]
    assert len(_group(spans)) == 2


def test_units_are_capped_at_span_boundaries():
    spans = [_span(i, f"Sentence number {i} of a long section.", "paragraph") for i in range(10)]
    units = _group(spans, max_chars=120)
    check_units(spans, units)
    assert len(units) > 1 and all(len(u.text) <= 120 for u in units)


def test_a_capped_table_repeats_its_header_row():
    header = _span(0, "| Parameter | Proposed | Actual |\n|---|---|---|", "table")
    rows = [_span(i, f"| Param {i} | {i}0 | {i}1 |", "table_row") for i in range(1, 9)]
    units = _group([header, *rows], max_chars=140)
    check_units([header, *rows], units)
    assert len(units) > 1
    for u in units[1:]:
        assert u.text.startswith("| Parameter | Proposed | Actual |")  # context for the extractor
        assert header.span_id not in [s.span_id for s in u.spans]     # the span itself belongs to one unit


def test_tags_are_carried_without_duplicates():
    spans = [TaggedSpan(span=_span(0, "Heading Washing temp: 60.", "heading"), tags=["washing"]),
             TaggedSpan(span=_span(1, "Time of wash: 30 min.", "paragraph"), tags=["washing", "time"])]
    (unit,) = asyncio.run(group_units(spans))
    assert unit.tags == ["washing", "time"]


def _heading_like() -> list[Span]:
    # 'Dyeing Route' is a heading the parse labelled as a plain paragraph
    return [_span(0, "Washing type: soft flow.", "paragraph"), _span(1, "Dyeing Route", "paragraph"),
            _span(2, "Liquor ratio: 1:8.", "paragraph")]


def test_without_a_decider_an_unlabelled_heading_does_not_split():
    assert len(_group(_heading_like())) == 1


def test_the_decider_sees_only_candidates_and_can_split():
    seen = []

    async def decider(texts):
        seen.append(texts)
        return [True] * len(texts)

    units = _group(_heading_like(), decider=decider)
    assert seen == [["Dyeing Route"]]  # one batched call, only the heading-like line
    assert [_norm(u.anchor.text) for u in units] == ["Washing type: soft flow.", "Dyeing Route"]


def test_a_failing_decider_degrades_to_no_split():
    async def decider(texts):
        raise RuntimeError("model down")

    assert len(_group(_heading_like(), decider=decider)) == 1


def test_segment_layout_without_layout_still_groups():
    text = "Washing\n\n| Parameter | Value |\n|---|---|\n| Temp | 60 |\n\nRemarks: none."
    units = _group(segment_layout("c:0:h", text, []))
    assert [(_norm(u.anchor.text), len(u.spans)) for u in units] == [
        ("Washing", 1), ("| Parameter | Value | |---|---|", 2), ("Remarks: none.", 1)]  # a table ends at its last row
