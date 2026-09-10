"""issue 0032 / CU-B5: the page<->char-offset map + per-span page lookup. Deterministic, no model/DB.

Reconstructs which source page each character range of the canonical document text came from (`prov[0].page_no`
threaded via ContentItem), so a span's `[doc_start, doc_end)` yields its page(s) -- a LIST, so a clause crossing
a page boundary reports both pages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from rag_wright.spans.page_map import build_page_offset_map, pages_for

_SEP = "\n\n"


@dataclass
class _Item:  # a minimal ContentItem stand-in (duck-typed on .text/.page/.bbox)
    text: str
    page: Optional[int]
    bbox: Optional[tuple] = None


def _canonical(items):
    return _SEP.join(i.text.strip() for i in items if i.text.strip())


def test_map_locates_each_item_and_assigns_its_page():
    items = [_Item("Clause one on page one.", 1), _Item("Clause two on page two.", 2, (1.0, 2.0, 3.0, 4.0))]
    canonical = _canonical(items)
    pm = build_page_offset_map(items, canonical)
    assert [pr.page for pr in pm] == [1, 2]
    # the second item's range slices back to its text
    assert canonical[pm[1].start:pm[1].end] == "Clause two on page two."


def test_span_within_one_page_returns_that_page_and_its_bbox():
    items = [_Item("Alpha clause.", 7), _Item("Beta clause here.", 8, (10.0, 20.0, 30.0, 40.0))]
    canonical = _canonical(items)
    pm = build_page_offset_map(items, canonical)
    s = canonical.index("Beta clause here.")
    pages, bbox = pages_for(pm, s, s + len("Beta clause here."))
    assert pages == [8]
    assert bbox == (10.0, 20.0, 30.0, 40.0)  # single overlapping item with a box -> carried


def test_span_crossing_a_page_boundary_returns_both_pages_and_no_single_bbox():
    items = [_Item("End of page seven.", 7, (1.0, 1.0, 2.0, 2.0)),
             _Item("Start of page eight.", 8, (3.0, 3.0, 4.0, 4.0))]
    canonical = _canonical(items)
    pm = build_page_offset_map(items, canonical)
    # a span spanning the tail of item 1 through the head of item 2
    start = canonical.index("page seven.")
    end = canonical.index("Start of") + len("Start of")
    pages, bbox = pages_for(pm, start, end)
    assert pages == [7, 8]  # LIST across the boundary
    assert bbox is None  # ambiguous across two items -> no single box


def test_items_without_a_page_contribute_nothing():
    items = [_Item("No provenance here.", None), _Item("Page three text.", 3)]
    canonical = _canonical(items)
    pm = build_page_offset_map(items, canonical)
    assert [pr.page for pr in pm] == [3]  # the page-less item is skipped
    s = canonical.index("Page three")
    pages, _ = pages_for(pm, s, s + len("Page three text."))
    assert pages == [3]


def test_no_overlap_or_missing_offsets_returns_empty():
    items = [_Item("Only clause.", 5)]
    pm = build_page_offset_map(items, _canonical(items))
    assert pages_for(pm, None, None) == ([], None)
    assert pages_for(pm, 10_000, 10_010) == ([], None)


def test_empty_map_when_no_provenance_at_all():
    items = [_Item("a", None), _Item("b", None)]
    assert build_page_offset_map(items, _canonical(items)) == []
