"""CHUNK-4 (ADR-0058, issue 0004, Tier-2 b1): StructuralModelFallbackDiscoverer -- structural boundaries first,
then a BOUNDED PER-SECTION model call only for sections that would be hard-split by the token cap. Hermetic: a
fake structural pass is not needed (the real one runs on labelled fake items), and the model fallback is an
injected stub, so no LLM. Asserts: only over-cap sections are refined, the model sees ONE section at a time
(never the whole doc), sub-indices map back correctly, structured docs make zero model calls, and order holds."""

from __future__ import annotations

from docling_core.types.doc.labels import DocItemLabel

from rag_wright.capabilities.rlm_chunking import (
    StructuralModelFallbackDiscoverer,
    _cuts_to_spans,
    _validate_partition,
)

H = DocItemLabel.SECTION_HEADER
B = DocItemLabel.TEXT
_BIG = "x" * 200   # one big item's text alone exceeds cap_chars below
_SMALL = "short"


class _Item:
    def __init__(self, label, text):
        self.label = label
        self.text = text


class _Doc:
    def __init__(self, rows):  # rows = [(label, text), ...]
        self.texts = [_Item(lb, tx) for lb, tx in rows]


class _FakeFallback:
    """A stub TagBoundaryDiscoverer: records the size of each per-section call and returns fixed sub-cuts
    (interpreted over the SUB document's 0-based index space via _cuts_to_spans)."""

    def __init__(self, sub_cuts):
        self._sub_cuts = sub_cuts
        self.calls = []  # one entry (the section's item count) per model call

    def discover(self, document):
        self.calls.append(len(document.texts))
        return _cuts_to_spans(self._sub_cuts, len(document.texts))

    async def adiscover(self, document):
        return self.discover(document)


def _pairs(spans):
    return [(s.start_index, s.end_index) for s in spans]


# token_cap=20 -> cap_chars=80; a single _BIG item (200 chars) exceeds it, a section of _SMALL items stays well
# under (comfortable margin regardless of the join separator).
def _disc(fallback):
    return StructuralModelFallbackDiscoverer(token_cap=20, fallback=fallback)


def test_over_cap_section_is_refined_by_a_per_section_call():
    fb = _FakeFallback(sub_cuts=[0, 2])  # split the section into [0,1] + [2,3]
    doc = _Doc([(H, "Heading"), (B, _BIG), (B, _BIG), (B, _BIG)])  # heading at 0 -> one structural span (0,3)
    spans = _disc(fb).discover(doc)
    assert _pairs(spans) == [(0, 1), (2, 3)]  # refined, sub-indices mapped back into the parent space
    assert fb.calls == [4]  # ONE per-section call over the section's 4 items -- never the whole doc separately


def test_under_cap_section_passes_through_with_no_model_call():
    fb = _FakeFallback(sub_cuts=[0, 1])
    doc = _Doc([(H, "Heading"), (B, _SMALL), (B, _SMALL)])  # structural span (0,2), under cap
    spans = _disc(fb).discover(doc)
    assert _pairs(spans) == [(0, 2)] and fb.calls == []  # kept structural boundary, zero model calls


def test_only_the_over_cap_section_is_refined_order_preserved():
    fb = _FakeFallback(sub_cuts=[0, 2])
    # structural cuts at the two headings -> spans (0,1) small, (2,4) over-cap
    doc = _Doc([(H, "A"), (B, _SMALL), (H, "B"), (B, _BIG), (B, _BIG)])
    spans = _disc(fb).discover(doc)
    assert _pairs(spans) == [(0, 1), (2, 3), (4, 4)]  # first span kept; second refined (+offset 2), order held
    assert fb.calls == [3]  # only the 3-item over-cap section went to the model


def test_single_over_cap_item_is_not_split_left_for_hard_split():
    fb = _FakeFallback(sub_cuts=[0])
    doc = _Doc([(B, _BIG)])  # one item, no heading -> structural span (0,0); cannot be split by boundaries
    spans = _disc(fb).discover(doc)
    assert _pairs(spans) == [(0, 0)] and fb.calls == []  # not refined -> finalize hard-splits the lone item


def test_result_is_a_valid_partition():
    fb = _FakeFallback(sub_cuts=[0, 2])
    doc = _Doc([(H, "A"), (B, _BIG), (B, _BIG), (B, _BIG)])
    spans = _disc(fb).discover(doc)
    _validate_partition(spans, len(doc.texts))  # raises if not contiguous/gap-free/covering


async def test_adiscover_refines_concurrently_and_preserves_order():
    fb = _FakeFallback(sub_cuts=[0, 1])
    # two separate over-cap sections (headings at 0 and 2), each refined per-section
    doc = _Doc([(H, "A"), (B, _BIG), (H, "B"), (B, _BIG)])
    spans = await _disc(fb).adiscover(doc)
    _validate_partition(spans, len(doc.texts))
    assert fb.calls.count(2) == 2  # two per-section calls, each over a 2-item section (never the whole doc)
    assert _pairs(spans) == [(0, 0), (1, 1), (2, 2), (3, 3)]  # both sections refined, order preserved
