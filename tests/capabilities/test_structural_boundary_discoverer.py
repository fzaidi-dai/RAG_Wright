"""CHUNK-2 (ADR-0058, issue 0004): the deterministic `StructuralBoundaryDiscoverer` -- boundaries from docling's
structural labels, NO model call. Hermetic: a fake document whose `.texts` items carry docling `DocItemLabel`s,
so there is nothing to time out and nothing to stub. Asserts a chunk starts at every heading, leading body is
its own span, structureless docs degrade to one span, and the partition is always valid."""

from __future__ import annotations

from docling_core.types.doc.labels import DocItemLabel

from rag_wright.capabilities.rlm_chunking import StructuralBoundaryDiscoverer, _validate_partition

H = DocItemLabel.SECTION_HEADER
T = DocItemLabel.TITLE
F = DocItemLabel.FIELD_HEADING
B = DocItemLabel.TEXT  # body -- not a heading


class _Item:
    def __init__(self, label):
        self.label = label
        self.text = "body"


class _Doc:
    def __init__(self, labels):
        self.texts = [_Item(lb) for lb in labels]


def _pairs(spans):
    return [(s.start_index, s.end_index) for s in spans]


def test_starts_a_chunk_at_each_heading():
    # headings at 0, 3, 7 over 10 items -> three sections, no model call
    spans = StructuralBoundaryDiscoverer().discover(_Doc([H, B, B, H, B, B, B, H, B, B]))
    assert _pairs(spans) == [(0, 2), (3, 6), (7, 9)]


def test_leading_body_before_the_first_heading_is_its_own_span():
    spans = StructuralBoundaryDiscoverer().discover(_Doc([B, B, H, B, B]))  # first heading at index 2
    assert _pairs(spans) == [(0, 1), (2, 4)]


def test_title_and_field_heading_also_start_chunks():
    spans = StructuralBoundaryDiscoverer().discover(_Doc([T, B, F, B]))
    assert _pairs(spans) == [(0, 1), (2, 3)]


def test_page_header_furniture_is_not_a_boundary():
    # PAGE_HEADER is running page furniture, not a section heading -> no cut there
    spans = StructuralBoundaryDiscoverer().discover(_Doc([H, B, DocItemLabel.PAGE_HEADER, B]))
    assert _pairs(spans) == [(0, 3)]  # only the heading at 0


def test_no_headings_degrades_to_one_span_over_the_whole_doc():
    spans = StructuralBoundaryDiscoverer().discover(_Doc([B, B, B, B]))
    assert _pairs(spans) == [(0, 3)]  # structureless -> one span (then cap-split downstream); CHUNK-4 improves


def test_empty_document_returns_no_spans():
    assert StructuralBoundaryDiscoverer().discover(_Doc([])) == []


def test_consecutive_headings_still_yield_a_valid_partition():
    # two headings in a row (a lone-heading span) -- must still be contiguous/gap-free (finalize folds it later)
    doc = _Doc([B, H, B, H, H, B])
    spans = StructuralBoundaryDiscoverer().discover(doc)
    _validate_partition(spans, len(doc.texts))  # raises if the partition is invalid
    assert _pairs(spans) == [(0, 0), (1, 2), (3, 3), (4, 5)]


async def test_adiscover_matches_discover():
    doc = _Doc([H, B, H, B])
    disc = StructuralBoundaryDiscoverer()
    assert _pairs(await disc.adiscover(doc)) == _pairs(disc.discover(doc))
