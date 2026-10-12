"""EV-3c: scorer 3 (extraction) and scorer 4 (judgment / verdict), on hand-worked cases."""
from __future__ import annotations

import pytest

from rag_wright.api import GoldItem, GoldSet, score_extraction, score_judgment


def _gold(rows) -> GoldSet:
    return GoldSet(name="g", version="1", items=[GoldItem(id=i, input=inp, expected=e, metadata=m)
                                                 for i, inp, e, m in rows])


# --- 3. extraction -------------------------------------------------------------------------------------------------

EXT = _gold([("d1", "short text", ["Acme Corp", "Beta LLC"], {}),
             ("d2", "a much longer text " * 20, ["Gamma Inc"], {}),
             ("d3", "medium length text here", [], {})])


def test_recall_and_precision_are_gated_apart():
    preds = {"d1": ["ACME Corp.", "Delta"], "d2": ["Gamma Inc", "Gamma Holdings", "Epsilon"], "d3": ["Zeta"]}
    r = score_extraction(EXT, preds, recall=0.6, precision=0.5)
    # micro: gold 3, matched 2; extracted (d1, d2) 5, matching 2. d3 has no gold: left out, its extras still listed
    assert r.gate == {"recall": pytest.approx(2 / 3), "precision": pytest.approx(2 / 5)}
    assert r.passed is False and r.diagnostics["f1"] == pytest.approx(2 * (2 / 3) * 0.4 / (2 / 3 + 0.4))
    assert r.diagnostics["no_gold"] == ["d3"]


def test_macro_averages_per_item_like_a_per_document_eval():
    preds = {"d1": ["Acme Corp"], "d2": ["Gamma Inc", "x", "y", "z"], "d3": []}
    r = score_extraction(EXT, preds, recall=0.0, precision=0.0, average="macro")
    assert r.gate == {"recall": pytest.approx((0.5 + 1.0) / 2), "precision": pytest.approx((1.0 + 0.25) / 2)}


def test_extras_and_misses_are_listed_and_recall_is_broken_down_by_input_length():
    r = score_extraction(EXT, {"d1": ["Acme Corp"], "d2": ["Omega"], "d3": []}, recall=0.0, precision=0.0)
    assert r.diagnostics["misses"] == ["d1: Beta LLC", "d2: Gamma Inc"]
    assert r.diagnostics["extras"] == ["d2: Omega"]
    by_len = r.diagnostics["recall_by_input_length"]
    assert [row["recall"] for row in by_len] == [0.5, 0.0]  # the short input, then the long one
    assert [row["n"] for row in by_len] == [1, 1]


def test_a_normaliser_and_a_strict_match_can_be_given():
    r = score_extraction(EXT, {"d1": ["acme corp", "Beta"], "d2": ["Gamma Inc"]}, recall=1.0, precision=1.0,
                         normalize=lambda s: s.lower(), match=lambda a, b: a == b)
    assert r.diagnostics["recall_hits"] == 2 and r.diagnostics["errors"] == 1  # d3 had no prediction


def test_nothing_extracted_has_zero_precision_for_that_item_in_macro():
    r = score_extraction(EXT, {"d1": [], "d2": ["Gamma Inc"], "d3": []}, recall=0.0, precision=0.0, average="macro")
    assert r.gate["precision"] == pytest.approx(0.5)


def test_an_item_listed_twice_counts_once():
    r = score_extraction(EXT, {"d1": ["Acme Corp", "ACME CORP.", "Beta LLC"], "d2": ["Gamma Inc"]}, recall=1.0,
                         precision=1.0)
    assert (r.diagnostics["extracted_items"], r.diagnostics["precision_hits"]) == (3, 3) and r.passed is True


# --- 4. judgment / verdict -----------------------------------------------------------------------------------------

JUD = _gold([("c1", "x", "violation", {"provenance": "real"}), ("c2", "x", "violation", {"provenance": "real"}),
             ("c3", "x", "compliant", {"provenance": "real"}), ("c4", "x", "compliant", {"provenance": "constructed"}),
             ("c5", "x", "not_addressed", {"provenance": "constructed"})])


def test_precision_and_recall_of_the_actionable_class_are_gated_apart():
    preds = {"c1": "violation", "c2": "compliant", "c3": "violation", "c4": "compliant", "c5": "not_addressed"}
    r = score_judgment(JUD, preds, actionable="violation", precision=0.5, recall=0.5)
    assert r.gate == {"precision": 0.5, "recall": 0.5} and r.passed is True
    d = r.diagnostics
    assert d["accuracy"] == pytest.approx(3 / 5) and d["false_alarm_rate"] == pytest.approx(1 / 3)
    assert d["per_class_recall"] == {"violation": 0.5, "compliant": 0.5, "not_addressed": 1.0}


def test_results_are_broken_down_by_provenance():
    preds = {"c1": "violation", "c2": "violation", "c3": "violation", "c4": "compliant", "c5": "violation"}
    r = score_judgment(JUD, preds, actionable="violation", precision=0.0, recall=0.0, by="provenance")
    assert r.diagnostics["by_provenance"]["real"] == {"n": 3, "accuracy": pytest.approx(2 / 3), "precision":
                                                      pytest.approx(2 / 3), "recall": 1.0}
    assert r.diagnostics["by_provenance"]["constructed"]["precision"] == 0.0


def test_a_missing_verdict_is_an_error_never_the_actionable_class():
    r = score_judgment(JUD, {"c1": "violation", "c3": "compliant", "c4": "compliant", "c5": "not_addressed"},
                       actionable="violation", precision=0.9, recall=0.9)
    assert r.diagnostics["errors"] == 1 and r.gate == {"precision": 1.0, "recall": 0.5} and r.passed is False


def test_calibration_bins_a_score_against_how_often_its_label_is_true():
    gold = _gold([(str(i), "x", lab, {}) for i, lab in enumerate([True, False, True, True, False])])
    preds = {"0": {"verdict": True, "score": 0.9}, "1": {"verdict": False, "score": 0.1},
             "2": {"verdict": False, "score": 0.3}, "3": {"verdict": True, "score": 1.0},
             "4": {"verdict": True, "score": 0.85}}
    r = score_judgment(gold, preds, actionable=False, precision=0.0, recall=0.0, score_label=True)
    assert r.diagnostics["calibration"] == [
        {"bin": "[0.0,0.2)", "n": 1, "share": 0.0}, {"bin": "[0.2,0.4)", "n": 1, "share": 1.0},
        {"bin": "[0.8,1.0]", "n": 3, "share": pytest.approx(2 / 3)}]
    assert r.gate["recall"] == 0.5  # catches 1 of the 2 False items
