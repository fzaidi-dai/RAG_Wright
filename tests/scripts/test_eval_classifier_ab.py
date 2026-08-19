"""Issue 0005 route-(b) quality A/B: the pure metric helpers (top-1 + typed accuracy / NONE no-function rate /
inter-path agreement). Hermetic -- no model. Ensures the number the A/B reports is computed correctly."""

from __future__ import annotations

from scripts.eval_classifier_guided_vs_tagparse import score_rows, top1


class _FS:
    def __init__(self, function):
        self.function = function


def test_top1_is_the_primary_or_none():
    assert top1([_FS("Cap On Liability"), _FS("Indemnification")]) == "Cap On Liability"
    assert top1([]) == "NONE"


def test_score_rows_typed_accuracy_and_none_rate_and_agreement():
    rows = [
        # typed spans: guided hits both, tag hits one (a miss) -> guided 1.0, tag 0.5 on typed
        {"gold": "Cap On Liability", "guided_top1": "Cap On Liability", "tag_top1": "Cap On Liability"},
        {"gold": "Governing Law", "guided_top1": "Governing Law", "tag_top1": "Indemnification"},
        # NONE spans: guided correctly no-function, tag false-positives once -> guided 1.0, tag 0.5 no-func rate
        {"gold": "NONE", "guided_top1": "NONE", "tag_top1": "NONE"},
        {"gold": "NONE", "guided_top1": "NONE", "tag_top1": "Audit Rights"},
    ]
    r = score_rows(rows)
    assert r["typed_n"] == 2 and r["none_n"] == 2
    assert r["guided_typed_top1"] == 1.0 and r["tag_typed_top1"] == 0.5
    assert r["guided_none_nofunc"] == 1.0 and r["tag_none_nofunc"] == 0.5
    assert r["agreement"] == 0.5  # rows 1 and 3 agree; rows 2 and 4 differ


def test_score_rows_handles_empty_strata():
    r = score_rows([{"gold": "NONE", "guided_top1": "NONE", "tag_top1": "NONE"}])
    assert r["typed_n"] == 0 and r["guided_typed_top1"] is None  # no typed spans -> None, not a crash
    assert r["none_n"] == 1 and r["guided_none_nofunc"] == 1.0
