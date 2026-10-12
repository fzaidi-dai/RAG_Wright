"""EV-6 (found in EV-3d): the generation-robustness report counts a failed call as an ERROR, never as an abstention
(ADR-0133), and leaves errors out of the abstain and flip rates."""
from __future__ import annotations

from scripts.measure_generation_robustness import strategy_summary


def test_an_error_is_counted_apart_never_as_an_abstention():
    # per query: (abstained, citations) per repeat; None = the call failed
    per_query = [[(False, 2), (None, 0), (False, 1)],   # answered twice, one error: no flip
                 [(True, 0), (True, 0), (None, 0)],     # abstained twice, one error
                 [(False, 1), (True, 0), (False, 3)]]   # a real flip
    s = strategy_summary(per_query)
    assert s["errors"] == 2 and s["runs"] == 9
    assert s["abstains"] == 3 and s["answered_runs"] == 7  # errors are not in the denominator
    assert s["flip_queries"] == 1
    assert s["mean_cites"] == (2 + 1 + 1 + 3) / 4
