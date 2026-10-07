"""ING-1 (ADR-0124): the generic ingestion hook contracts and the engine-enforced checks.

The engine owns ingestion's wiring; a domain overrides only the domain-shaped hooks (segmenter, span tagger, unit
grouper, boundary decider, extractor, writer). These tests pin what every hook's output must satisfy, so a domain's
override is checked the same way as the engine default and the reference pack's legal implementations."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from rag_wright.api import (
    IngestionContractError,
    KgEdge,
    KgNode,
    LayoutItem,
    Span,
    TaggedSpan,
    Unit,
    UnitExtraction,
    check_extraction,
    check_tiling,
    check_units,
)

CHUNK = "doc-1:0:abc"
# messy on purpose: brackets, quotes, newlines, a markdown table (clean fixtures hide real-text bugs)
TEXT = (
    'Fabric "A" [ref. ISO 105-C06]: 65% polyester; 35% cotton.\n'
    "| test | grade |\n| wash | 4-5 |\n"
    "Shrinkage (warp) <= 3%.\n"
)


def _spans(text: str, cuts: list[int], chunk_id: str = CHUNK) -> list[Span]:
    return [Span(span_id=f"{chunk_id}#{i}", parent_chunk_id=chunk_id, span_index=i, start=s, end=e, text=text[s:e])
            for i, (s, e) in enumerate(zip(cuts, cuts[1:]))]


def _good() -> list[Span]:
    a = TEXT.index("|")
    b = TEXT.index("Shrinkage")
    return _spans(TEXT, [0, a, b, len(TEXT)])


# --- spans: the segmenter's output must tile the chunk text byte-faithfully under the span_id scheme ---

def test_tiling_accepts_a_byte_faithful_partition():
    check_tiling(CHUNK, TEXT, _good())


def test_tiling_accepts_empty_text_with_no_spans():
    check_tiling(CHUNK, "", [])


def test_tiling_rejects_a_gap():
    spans = _good()
    spans[1] = spans[1].model_copy(update={"start": spans[1].start + 1, "text": TEXT[spans[1].start + 1:spans[1].end]})
    with pytest.raises(IngestionContractError, match="tile"):
        check_tiling(CHUNK, TEXT, spans)


def test_tiling_rejects_text_that_is_not_the_slice():
    spans = _good()
    spans[0] = spans[0].model_copy(update={"text": spans[0].text.strip()})
    with pytest.raises(IngestionContractError, match="slice"):
        check_tiling(CHUNK, TEXT, spans)


def test_tiling_rejects_a_span_id_off_the_scheme():
    spans = _good()
    spans[2] = spans[2].model_copy(update={"span_id": "doc-1:0:abc/2"})
    with pytest.raises(IngestionContractError, match="span_id"):
        check_tiling(CHUNK, TEXT, spans)


def test_tiling_rejects_a_span_from_another_chunk():
    spans = _spans(TEXT, [0, len(TEXT)], chunk_id="doc-1:1:def")
    with pytest.raises(IngestionContractError, match="parent_chunk_id"):
        check_tiling(CHUNK, TEXT, spans)


def test_span_rejects_end_before_start():
    with pytest.raises(ValidationError):
        Span(span_id=f"{CHUNK}#0", parent_chunk_id=CHUNK, span_index=0, start=5, end=2, text="")


def test_the_reference_segmenter_meets_the_contract():
    """The reference pack's legal segmenter is one implementation of the hook; its output passes the same check."""
    from rag_wright.spans.segment import OperativeSpan, segment_clause

    assert OperativeSpan is Span  # one span contract, not a legal-only twin
    body = '12.1 Limitation. (a) Neither party [except "Licensor"] shall; (b) the cap is $1.5M.\n13. Term.'
    check_tiling(CHUNK, body, segment_clause(CHUNK, body))


# --- layout: the domain-neutral view of the parse a segmenter / grouper may consult ---

def test_layout_item_kind_is_a_closed_set():
    LayoutItem(kind="heading", text="Fabric spec", start=0, end=11, level=1)
    with pytest.raises(ValidationError):
        LayoutItem(kind="section_header", text="x", start=0, end=1)  # docling's label, not the engine's kind


def test_layout_item_rejects_end_before_start():
    with pytest.raises(ValidationError):
        LayoutItem(kind="paragraph", text="x", start=3, end=1)


# --- units: the grouper's output (the extraction unit) ---

def test_unit_anchor_must_be_a_member():
    spans = _good()
    with pytest.raises(ValidationError):
        Unit(index=0, anchor=spans[2], spans=spans[:2], text="x")


def test_unit_needs_text():
    spans = _good()
    with pytest.raises(ValidationError):
        Unit(index=0, anchor=spans[0], spans=spans[:1], text="  \n")


def test_units_accept_ordered_disjoint_groups_and_may_drop_spans():
    spans = _good()
    units = [Unit(index=0, anchor=spans[0], spans=spans[:2], text="a"),
             Unit(index=1, anchor=spans[2], spans=spans[2:], text="b")]
    check_units(spans, units)
    check_units(spans, units[:1])  # a dropped span (e.g. page furniture) is allowed: it stays in the span index


def test_units_reject_a_span_used_twice():
    spans = _good()
    units = [Unit(index=0, anchor=spans[0], spans=spans[:2], text="a"),
             Unit(index=1, anchor=spans[1], spans=spans[1:], text="b")]
    with pytest.raises(IngestionContractError, match="more than one unit"):
        check_units(spans, units)


def test_units_reject_out_of_order_and_bad_indices():
    spans = _good()
    with pytest.raises(IngestionContractError, match="order"):
        check_units(spans, [Unit(index=0, anchor=spans[2], spans=[spans[2], spans[0]], text="a")])
    with pytest.raises(IngestionContractError, match="index"):
        check_units(spans, [Unit(index=3, anchor=spans[0], spans=spans[:1], text="a")])


def test_units_reject_an_unknown_span():
    spans = _good()
    stranger = _spans("zz", [0, 2], chunk_id="doc-9:0:fff")[0]
    with pytest.raises(IngestionContractError, match="unknown span"):
        check_units(spans, [Unit(index=0, anchor=stranger, spans=[stranger], text="zz")])


def test_tagged_span_defaults_to_no_tags():
    assert TaggedSpan(span=_good()[0]).tags == []


# --- extraction: records carry provenance (span) + confidence (FR-S.4) ---

def _unit() -> Unit:
    spans = _good()
    return Unit(index=0, anchor=spans[0], spans=spans[:2], text="x")


def _node(**props) -> KgNode:
    return KgNode(type="FabricSpec", key_field="spec_id", props={"spec_id": "s1", **props})


def test_extraction_accepts_records_anchored_in_the_unit():
    unit = _unit()
    ext = UnitExtraction(nodes=[_node(span_id=unit.spans[1].span_id, confidence="EXTRACTED")],
                         edges=[KgEdge(type="UsesStandard", from_type="FabricSpec", from_key_field="spec_id",
                                       from_key="s1", to_type="Standard", to_key_field="code",
                                       to_key="ISO 105-C06", props={})])
    check_extraction(unit, ext)


def test_extraction_rejects_a_record_without_provenance():
    with pytest.raises(IngestionContractError, match="span_id"):
        check_extraction(_unit(), UnitExtraction(nodes=[_node(confidence="EXTRACTED")]))


def test_extraction_rejects_a_span_outside_the_unit():
    with pytest.raises(IngestionContractError, match="span_id"):
        check_extraction(_unit(), UnitExtraction(nodes=[_node(span_id=f"{CHUNK}#2", confidence="EXTRACTED")]))


def test_extraction_rejects_an_unknown_confidence():
    unit = _unit()
    with pytest.raises(IngestionContractError, match="confidence"):
        check_extraction(unit, UnitExtraction(nodes=[_node(span_id=unit.anchor.span_id, confidence="SURE")]))


def test_the_boundary_decider_contract_is_shared():
    from rag_wright.api import BoundaryDecider
    from rag_wright.spans import boundary

    assert boundary.BoundaryDecider is BoundaryDecider


def test_span_kind_is_optional_and_closed():
    span = _good()[0]
    assert span.kind is None  # spans from code that does not set it are plain text to a grouper
    assert span.model_copy(update={"kind": "table_row"}).kind == "table_row"
    with pytest.raises(ValidationError):
        Span(span_id=f"{CHUNK}#0", parent_chunk_id=CHUNK, span_index=0, start=0, end=1, text="x", kind="row")
