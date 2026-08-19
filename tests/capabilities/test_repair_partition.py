"""CU-B4: `repair_partition` must turn ANY (start,end) index ranges from a single-call model into a VALID
partition of [0, n) -- contiguous, gap-free, covering every item, ordered, non-empty. The single call (unlike
the agentic coverage-tail) gives no coverage guarantee, so repair is the safety net; it is tested exhaustively
here (per the standing gate) before any full-holdout ingest. Hermetic -- no LLM, no network."""

from __future__ import annotations

import random

import pytest

from types import SimpleNamespace

from rag_wright.capabilities.rlm_chunking import (
    BoundarySpan,
    SingleCallBoundaryDiscoverer,
    _BoundaryList,
    _validate_partition,
    repair_partition,
)


def _pairs(spans: list[BoundarySpan]) -> list[tuple[int, int]]:
    return [(s.start_index, s.end_index) for s in spans]


def _is_valid_partition(spans: list[BoundarySpan], n: int) -> bool:
    """A partition of [0, n): starts at 0, ends at n-1, contiguous (no gap/overlap), each span non-empty."""
    try:
        _validate_partition(spans, n)  # the same gate chunk() applies after discover()
    except Exception:  # noqa: BLE001
        return False
    return True


# --- the malformed-input classes a single-call model actually produces ------------------------------

def test_already_valid_partition_is_preserved():
    raw = [(0, 2), (3, 5), (6, 9)]  # n=10, clean
    out = repair_partition(raw, 10)
    assert _pairs(out) == [(0, 2), (3, 5), (6, 9)]  # breaks {3,6} -> identical
    assert _is_valid_partition(out, 10)


def test_overlapping_spans_are_repaired():
    raw = [(0, 5), (3, 9)]  # overlap on 3,4,5 -- starts {0,3} -> break {3}
    out = repair_partition(raw, 10)
    assert _pairs(out) == [(0, 2), (3, 9)]
    assert _is_valid_partition(out, 10)


def test_gapped_spans_absorb_the_gap_no_item_lost():
    raw = [(0, 4), (8, 12)]  # items 5,6,7 uncovered -- starts {0,8} -> break {8}
    out = repair_partition(raw, 13)
    assert _pairs(out) == [(0, 7), (8, 12)]  # gap items land in the preceding chunk; nothing dropped
    assert _is_valid_partition(out, 13)


def test_out_of_range_indices_are_dropped():
    raw = [(-3, 2), (4, 100), (7, 7)]  # break candidates 4,7 valid; -3 dropped
    out = repair_partition(raw, 10)
    assert _pairs(out) == [(0, 3), (4, 6), (7, 9)]
    assert _is_valid_partition(out, 10)


def test_start_greater_than_end_still_uses_the_start_as_a_break():
    raw = [(0, 3), (7, 2)]  # garbage end -- start 7 is a valid break
    out = repair_partition(raw, 10)
    assert _pairs(out) == [(0, 6), (7, 9)]
    assert _is_valid_partition(out, 10)


def test_unordered_and_duplicate_breaks_are_sorted_and_deduped():
    raw = [(6, 9), (3, 5), (3, 4), (0, 2), (6, 8)]  # duplicate 3 and 6, out of order
    out = repair_partition(raw, 10)
    assert _pairs(out) == [(0, 2), (3, 5), (6, 9)]
    assert _is_valid_partition(out, 10)


def test_empty_raw_yields_single_whole_document_span():
    out = repair_partition([], 10)
    assert _pairs(out) == [(0, 9)]  # no break -> one coarse-but-valid chunk
    assert _is_valid_partition(out, 10)


def test_no_valid_break_falls_back_to_whole_document():
    raw = [(0, 9), (-1, 0), (10, 12)]  # only start 0 (not a break) plus out-of-range
    out = repair_partition(raw, 10)
    assert _pairs(out) == [(0, 9)]
    assert _is_valid_partition(out, 10)


def test_break_at_zero_is_ignored_partition_still_starts_at_zero():
    raw = [(0, 4), (0, 4), (5, 9)]  # start 0 is never a break (already the left edge)
    out = repair_partition(raw, 10)
    assert _pairs(out) == [(0, 4), (5, 9)]
    assert _is_valid_partition(out, 10)


def test_single_item_document():
    assert repair_partition([(0, 0)], 1) == [BoundarySpan(start_index=0, end_index=0)]
    assert repair_partition([(0, 5), (2, 9)], 1) == [BoundarySpan(start_index=0, end_index=0)]  # breaks clamped


@pytest.mark.parametrize("n", [0, -1, -100])
def test_non_positive_n_yields_empty(n):
    assert repair_partition([(0, 5)], n) == []


def test_every_break_becomes_a_boundary_maximal_segmentation():
    raw = [(i, i) for i in range(6)]  # each item its own span -> starts {1,2,3,4,5} all breaks
    out = repair_partition(raw, 6)
    assert _pairs(out) == [(0, 0), (1, 1), (2, 2), (3, 3), (4, 4), (5, 5)]
    assert _is_valid_partition(out, 6)


# --- property test: for ANY garbage input, the output is ALWAYS a valid partition -------------------

def _doc(n: int):
    return SimpleNamespace(texts=[SimpleNamespace(text=f"item {i}", label="text", level=None) for i in range(n)])


class _FakeStructured:
    def __init__(self, spans):
        self._spans = spans

    def invoke(self, _prompt):
        return _BoundaryList(spans=self._spans)


def test_discoverer_applies_repair_to_a_garbage_model_partition():
    # model returns an overlapping + out-of-range mess; the discoverer must still hand chunk() a valid partition
    garbage = [BoundarySpan(start_index=0, end_index=8), BoundarySpan(start_index=3, end_index=20)]
    disc = SingleCallBoundaryDiscoverer(model_id="fake",
                                        structured_factory=lambda _m, _s, **_kw: _FakeStructured(garbage))
    spans = disc.discover(_doc(10))
    assert _pairs(spans) == [(0, 2), (3, 9)]  # start 3 the only in-range break
    assert _is_valid_partition(spans, 10)


def test_discoverer_empty_document_returns_no_spans():
    disc = SingleCallBoundaryDiscoverer(model_id="fake",
                                        structured_factory=lambda _m, _s, **_kw: _FakeStructured([]))
    assert disc.discover(_doc(0)) == []


def test_property_output_is_always_a_valid_partition():
    rng = random.Random(0)
    for _ in range(3000):
        n = rng.randint(1, 40)
        k = rng.randint(0, 12)
        # deliberately adversarial: negatives, out-of-range, reversed, duplicates, huge values
        raw = [(rng.randint(-5, n + 5), rng.randint(-5, n + 5)) for _ in range(k)]
        out = repair_partition(raw, n)
        assert _is_valid_partition(out, n), f"invalid partition for n={n} raw={raw} -> {_pairs(out)}"
        # coverage sanity: the spans tile exactly [0, n-1]
        covered = [i for s in out for i in range(s.start_index, s.end_index + 1)]
        assert covered == list(range(n))
