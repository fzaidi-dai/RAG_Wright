"""EV-3a: scorer 1 (classification / typed decision) and scorer 8 (candidate-then-choose), on hand-worked cases."""
from __future__ import annotations

import pytest

from rag_wright.api import GoldItem, GoldSet, score_candidate_choice, score_classification


def _gold(pairs) -> GoldSet:
    return GoldSet(name="g", version="1", items=[GoldItem(id=i, input="x", expected=e) for i, e in pairs])


# --- 1. classification ---------------------------------------------------------------------------------------------

GOLD = _gold([("1", "cap"), ("2", "cap"), ("3", "cap"), ("4", "uncapped"), ("5", "uncapped"), ("6", "NONE")])


def test_the_gate_is_the_weakest_class_not_the_average():
    preds = {"1": "cap", "2": "cap", "3": "cap", "4": "uncapped", "5": "cap", "6": "NONE"}
    r = score_classification(GOLD, preds, floor=0.6)
    assert r.diagnostics["per_class"]["uncapped"] == {"n": 2, "recall": 0.5}
    assert r.gate["min_class_recall@1"] == 0.5 and r.passed is False
    assert r.diagnostics["top1_accuracy"] == pytest.approx(5 / 6)


def test_top_k_counts_a_label_ranked_within_k_and_reports_the_tag_budget():
    preds = {"1": ["cap", "uncapped"], "2": ["cap"], "3": {"cap": 0.9, "uncapped": 0.1},
             "4": ["cap", "uncapped"], "5": {"uncapped": 0.6, "cap": 0.4}, "6": ["NONE"]}
    one = score_classification(GOLD, preds, floor=0.9, k=1)
    two = score_classification(GOLD, preds, floor=0.9, k=2)
    assert one.diagnostics["per_class"]["uncapped"]["recall"] == 0.5
    assert two.diagnostics["per_class"]["uncapped"]["recall"] == 1.0 and two.passed is True
    assert two.diagnostics["tags_per_item"] == pytest.approx((2 + 1 + 2 + 2 + 2 + 1) / 6)


def test_top1_is_gated_when_something_acts_on_the_main_label():
    preds = {"1": ["uncapped", "cap"], "2": ["cap"], "3": ["cap"], "4": ["uncapped"], "5": ["uncapped"],
             "6": ["NONE"]}
    r = score_classification(GOLD, preds, floor=0.5, k=2, top1_floor=0.9)
    assert r.gate == {"min_class_recall@2": 1.0, "top1_accuracy": pytest.approx(5 / 6)}
    assert r.passed is False and r.thresholds == {"min_class_recall@2": 0.5, "top1_accuracy": 0.9}


def test_excluded_classes_are_scored_but_not_gated():
    preds = {"1": "cap", "2": "cap", "3": "cap", "4": "uncapped", "5": "uncapped", "6": "cap"}
    r = score_classification(GOLD, preds, floor=0.9, exclude=["NONE"])
    assert r.passed is True and r.diagnostics["per_class"]["NONE"]["recall"] == 0.0
    assert r.diagnostics["mean_class_recall@1"] == 1.0


def test_a_missing_prediction_is_an_error_counted_wrong():
    preds = {"1": "cap", "2": "cap", "4": "uncapped", "5": "uncapped", "6": "NONE"}  # 3 failed
    r = score_classification(GOLD, preds, floor=0.5)
    assert r.diagnostics["errors"] == 1 and r.diagnostics["per_class"]["cap"]["recall"] == pytest.approx(2 / 3)


def test_confusions_pair_the_gold_with_the_top_prediction():
    preds = {"1": "uncapped", "2": "uncapped", "3": "cap", "4": "cap", "5": "uncapped", "6": "NONE"}
    r = score_classification(GOLD, preds, floor=0.5)
    assert r.diagnostics["confusions"][:2] == [{"gold": "cap", "predicted": "uncapped", "count": 2},
                                               {"gold": "uncapped", "predicted": "cap", "count": 1}]


def test_a_multi_label_gold_item_counts_once_per_gold_label():
    gold = _gold([("1", ["cap", "carve_out"]), ("2", "cap")])
    r = score_classification(gold, {"1": ["cap", "carve_out"], "2": ["cap"]}, floor=1.0, k=2)
    assert r.diagnostics["per_class"] == {"cap": {"n": 2, "recall": 1.0}, "carve_out": {"n": 1, "recall": 1.0}}


# --- 8. candidate-then-choose --------------------------------------------------------------------------------------

def _cc(pairs) -> GoldSet:
    return GoldSet(name="g", version="1", items=[GoldItem(id=i, input="x", expected=e) for i, e in pairs])


CC = _cc([("p1", {"values": [["cap_amount", "$5,000,000"], ["term", "12 months"]], "ignore": ["30 days"]}),
          ("p2", {"values": [["jurisdiction", "New York"]]}),
          ("p3", {"values": []})])


def test_value_level_recall_and_precision_need_the_right_key():
    preds = {"p1": {"candidates": ["$5,000,000", "12 months", "30 days"],
                    "values": [["cap_amount", "$5,000,000."], ["term", "30 days"], ["notice", "12 months"]]},
             "p2": {"candidates": ["New York", "Delaware"], "values": [["jurisdiction", "the State of New York"]]},
             "p3": {"candidates": [], "values": [["term", "two years"]]}}
    r = score_candidate_choice(CC, preds, recall=0.5, precision=0.5, coverage=0.9)
    d = r.diagnostics
    assert (d["recall_hits"], d["expected_values"]) == (2, 3)  # term was emitted under the wrong key
    assert (d["precision_hits"], d["emitted_values"]) == (2, 4)  # "30 days" is ignored, not counted
    assert r.gate == {"coverage": 1.0, "recall": pytest.approx(2 / 3), "precision": 0.5}
    assert r.passed is True


def test_coverage_caps_recall_and_is_gated():
    preds = {"p1": {"candidates": ["$5,000,000"], "values": [["cap_amount", "$5,000,000"]]},
             "p2": {"candidates": ["Delaware"], "values": []}, "p3": {"candidates": [], "values": []}}
    r = score_candidate_choice(CC, preds, recall=0.0, precision=0.0, coverage=0.9)
    assert r.gate["coverage"] == pytest.approx(1 / 3) and r.passed is False
    assert r.diagnostics["coverage_misses"] == ["p1: 12 months", "p2: New York"]


def test_emitting_more_values_is_not_a_win():
    many = {"p1": {"candidates": ["$5,000,000", "12 months"],
                   "values": [["cap_amount", "$5,000,000"], ["term", "12 months"], ["term", "1 year"],
                              ["cap_amount", "$1"], ["term", "monthly"]]},
            "p2": {"candidates": ["New York"], "values": [["jurisdiction", "New York"], ["jurisdiction", "Ohio"]]},
            "p3": {"candidates": [], "values": []}}
    r = score_candidate_choice(CC, many, recall=0.9, precision=0.8, coverage=0.9)
    assert r.gate["recall"] == 1.0 and r.gate["precision"] == pytest.approx(3 / 7) and r.passed is False
    assert r.diagnostics["values_per_item"] == pytest.approx(7 / 3)


def test_a_missing_prediction_is_an_error_and_recalls_nothing():
    r = score_candidate_choice(CC, {"p2": {"candidates": ["New York"], "values": [["jurisdiction", "New York"]]},
                                    "p3": {"candidates": [], "values": []}}, recall=0.5, precision=0.5, coverage=0.5)
    assert r.diagnostics["errors"] == 1 and r.gate["recall"] == pytest.approx(1 / 3)
