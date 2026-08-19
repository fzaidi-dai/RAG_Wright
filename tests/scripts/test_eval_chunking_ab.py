"""CHUNK-5 (ADR-0058): the A/B eval's PURE metric helpers -- hermetic, no IO/model. Proves the numbers the gate
reports are computed correctly (boundary agreement, heading alignment, size stats), so the gate signal is
trustworthy independent of any corpus run."""

from __future__ import annotations

from docling_core.types.doc.labels import DocItemLabel

from rag_wright.capabilities.rlm_chunking import BoundarySpan
from scripts.eval_chunking_ab import boundary_agreement, heading_alignment, size_stats


def _spans(pairs):
    return [BoundarySpan(start_index=a, end_index=b) for a, b in pairs]


class _Item:
    def __init__(self, label):
        self.label = label


class _Doc:
    def __init__(self, labels):
        self.texts = [_Item(lb) for lb in labels]


def test_boundary_agreement_identical_is_one():
    a = _spans([(0, 2), (3, 5)])
    assert boundary_agreement(a, a) == 1.0


def test_boundary_agreement_partial_overlap_is_jaccard_of_cut_starts():
    a = _spans([(0, 2), (3, 5), (6, 9)])  # starts {0,3,6}
    b = _spans([(0, 5), (6, 9)])          # starts {0,6}
    assert boundary_agreement(a, b) == 2 / 3  # intersection {0,6}=2, union {0,3,6}=3


def test_boundary_agreement_two_empty_partitions_agree():
    assert boundary_agreement([], []) == 1.0


def test_heading_alignment_counts_chunks_starting_at_a_heading():
    H, B = DocItemLabel.SECTION_HEADER, DocItemLabel.TEXT
    doc = _Doc([H, B, B, H, B, B, B])          # headings at 0 and 3
    spans = _spans([(0, 2), (3, 6)])            # both chunks start at a heading -> 1.0
    assert heading_alignment(doc, spans) == 1.0
    spans2 = _spans([(0, 1), (2, 4), (5, 6)])   # only the first starts at a heading -> 1/3
    assert round(heading_alignment(doc, spans2), 3) == round(1 / 3, 3)


def test_heading_alignment_of_a_fixed_size_split_is_low():
    B = DocItemLabel.TEXT
    doc = _Doc([B] * 6)  # no headings at all
    assert heading_alignment(doc, _spans([(0, 2), (3, 5)])) == 0.0


def test_size_stats_distribution():
    stats = size_stats(["a" * 10, "b" * 30, "c" * 50])
    assert stats["n_chunks"] == 3 and stats["p50"] == 30 and stats["max"] == 50


def test_size_stats_empty():
    assert size_stats([]) == {"n_chunks": 0, "p50": 0, "p95": 0, "max": 0}
