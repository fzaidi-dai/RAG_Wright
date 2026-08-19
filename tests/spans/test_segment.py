"""T55 (FR-R, ADR-0025): tests for the operative-span segmenter.

Hermetic and deterministic (no model). The load-bearing invariant is byte-faithful tiling -- the spans must
reconstruct the clause exactly -- so no operative text is ever lost or duplicated. The rest asserts the legal
structure is split (enumeration, sentences) while abbreviations and section references are NOT false-split.
"""

from __future__ import annotations

from rag_wright.spans.segment import DEFAULT_MIN_CHARS, OperativeSpan, segment_clause

_MESSY = (
    "12. LIMITATION OF LIABILITY AND WARRANTIES.\n"
    "(a) In no event shall Supplier be liable for consequential [see 9.1] or indirect damages.\n"
    '(b) Supplier\'s total liability shall not exceed the fees paid in the prior 12 months.\n'
    '(c) The Products are provided "AS-IS".'
)


def _reconstructs(body: str) -> list[OperativeSpan]:
    spans = segment_clause("aaa1:0:hash", body, parent_okf_path="limitation-of-liability/aaa1.md")
    assert "".join(s.text for s in spans) == body  # byte-faithful tiling: nothing lost/duplicated/reordered
    for s in spans:  # offsets are self-consistent
        assert body[s.start : s.end] == s.text
    for a, b in zip(spans, spans[1:]):  # contiguous, gap-free, ordered
        assert a.end == b.start
    return spans


# --- the load-bearing invariant --------------------------------------------------------------


def test_byte_faithful_tiling_on_messy_multi_provision():
    spans = _reconstructs(_MESSY)
    assert len(spans) >= 3  # the three enumerated provisions are separated


def test_empty_body_yields_no_spans():
    assert segment_clause("x:0:h", "") == []


# --- structural splitting --------------------------------------------------------------------


def test_enumeration_markers_split_provisions():
    spans = _reconstructs(_MESSY)
    joined = [s.text for s in spans]
    assert any(t.lstrip().startswith("(a)") for t in joined)
    assert any(t.lstrip().startswith("(b)") for t in joined)
    assert any(t.lstrip().startswith("(c)") for t in joined)


def test_sentence_boundary_splits():
    spans = _reconstructs("Losses are capped at fees paid. Warranties are disclaimed entirely by Supplier.")
    assert len(spans) == 2


# --- do NOT false-split ----------------------------------------------------------------------


def test_abbreviation_is_not_a_boundary():
    # "Corp." is a known abbreviation -> the following capitalized word must not start a new span
    spans = _reconstructs("Amounts are payable to Acme Corp. Losses under this clause are capped at cost.")
    assert len(spans) == 1


def test_section_reference_decimal_is_not_a_boundary():
    # "12.1" is a section reference, not a sentence end -> one span
    spans = _reconstructs("Liability under Section 12.1 is limited to direct damages only.")
    assert len(spans) == 1


# --- sub-floor merge, ids, determinism -------------------------------------------------------


def test_bare_heading_folds_into_following_provision():
    body = "12.1. In no event shall either party be liable for consequential damages of any kind whatsoever."
    spans = _reconstructs(body)
    # a lone "12.1." marker (< min_chars) must not be its own span
    assert all(len(s.text.strip()) >= DEFAULT_MIN_CHARS or len(spans) == 1 for s in spans)
    assert not any(s.text.strip().rstrip(".").replace(".", "").isdigit() for s in spans)  # no bare-number span


def test_numbered_title_heading_folds_into_body_not_standalone():
    # 0006-B: "9. Limitation of Liability" (26 chars, ABOVE the 25 sub-floor) must NOT be a standalone span --
    # a standalone heading gets classified as a clause pointing at a bare heading (issue 0006 Problem 2).
    body = "9. Limitation of Liability\n\nSupplier's total liability shall not exceed the fees paid."
    spans = segment_clause("c:0:h", body)
    texts = [s.text.strip() for s in spans]
    assert "9. Limitation of Liability" not in texts                       # no standalone heading-only span
    assert any("Limitation of Liability" in t and "total liability" in t for t in texts)  # heading stays with body
    assert "".join(s.text for s in spans) == body                          # tiling invariant preserved


def test_unnumbered_title_heading_also_folds():
    body = "Term and Renewal\n\nThis Agreement renews automatically for successive one-year terms."
    spans = segment_clause("c:0:h", body)
    assert "Term and Renewal" not in [s.text.strip() for s in spans]       # not standalone
    assert "".join(s.text for s in spans) == body


def test_short_body_sentence_is_not_treated_as_a_heading():
    # a genuine short provision (has a sentence terminator) is NOT a heading -- must not wrongly fold away
    body = "10. Governing Law\n\nThis Agreement is governed by New York law. The parties consent to jurisdiction."
    spans = segment_clause("c:0:h", body)
    assert any("governed by New York law" in s.text for s in spans)        # the provision survives
    assert "".join(s.text for s in spans) == body


def test_span_id_embeds_parent_and_index():
    spans = _reconstructs(_MESSY)
    for i, s in enumerate(spans):
        assert s.span_id == f"aaa1:0:hash#{i}"
        assert s.parent_chunk_id == "aaa1:0:hash"
        assert s.parent_okf_path == "limitation-of-liability/aaa1.md"
        assert s.span_index == i


def test_segmentation_is_deterministic():
    a = [(s.start, s.end, s.text) for s in segment_clause("c:0:h", _MESSY)]
    b = [(s.start, s.end, s.text) for s in segment_clause("c:0:h", _MESSY)]
    assert a == b
