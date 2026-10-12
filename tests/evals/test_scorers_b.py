"""EV-3b: scorer 2 (retrieval) and scorer 6 (graph / relational), on hand-worked cases."""
from __future__ import annotations

import math

import pytest

from rag_wright.api import GoldItem, GoldSet, score_relational, score_retrieval


def _gold(pairs, **meta) -> GoldSet:
    return GoldSet(name="g", version="1", items=[
        GoldItem(id=i, input="q", expected=e, metadata=meta.get(i, {})) for i, e in pairs])


# --- 2. retrieval --------------------------------------------------------------------------------------------------

GRADED = _gold([("q1", {"a": 3, "b": 2, "c": 1}), ("q2", {"x": 4}), ("q3", {"y": 2, "z": 0})])


def test_recall_at_k_counts_only_relevant_grades_and_gates():
    ranked = {"q1": ["c", "a", "zz", "b"], "q2": ["n1", "n2", "x"], "q3": ["z", "y"]}
    r = score_retrieval(GRADED, ranked, k=2, recall=0.5, relevant_grade=2)
    # q1: relevant {a, b}; top-2 holds a -> 0.5. q2: x at rank 3 -> 0. q3: y at rank 2 -> 1.0
    assert r.gate == {"recall@2": pytest.approx(0.5)} and r.passed is True
    assert r.diagnostics["per_item_recall@2"] == {"q1": 0.5, "q2": 0.0, "q3": 1.0}


def test_ndcg_uses_graded_exponential_gain_and_is_never_gated():
    ranked = {"q1": ["c", "a", "b"], "q2": ["x"], "q3": ["y"]}
    r = score_retrieval(GRADED, ranked, k=3, recall=1.0, relevant_grade=2, ndcg_k=3)
    dcg = (2 ** 1 - 1) / math.log2(2) + (2 ** 3 - 1) / math.log2(3) + (2 ** 2 - 1) / math.log2(4)
    idcg = (2 ** 3 - 1) / math.log2(2) + (2 ** 2 - 1) / math.log2(3) + (2 ** 1 - 1) / math.log2(4)
    assert r.diagnostics["ndcg@3"] == pytest.approx((dcg / idcg + 1 + 1) / 3)
    assert "ndcg@3" not in r.gate and r.passed is True


def test_a_plain_list_of_relevant_ids_is_binary_relevance():
    gold = _gold([("q1", ["a", "b"])])
    r = score_retrieval(gold, {"q1": ["a", "c", "b"]}, k=2, recall=0.5, also_recall_at=[3])
    assert r.gate == {"recall@2": 0.5} and r.diagnostics["recall@3"] == 1.0


def test_an_item_with_nothing_relevant_is_left_out_not_counted_as_perfect():
    gold = _gold([("q1", {"a": 3}), ("q2", {"b": 1})])  # q2 has nothing at grade >= 2
    r = score_retrieval(gold, {"q1": [], "q2": ["b"]}, k=5, recall=0.5, relevant_grade=2)
    assert r.gate == {"recall@5": 0.0} and r.diagnostics["scored"] == 1 and r.diagnostics["no_relevant"] == ["q2"]


def test_a_missing_ranking_is_an_error_that_recalls_nothing():
    r = score_retrieval(GRADED, {"q1": ["a", "b"], "q3": ["y"]}, k=2, recall=0.5, relevant_grade=2)
    assert r.diagnostics["errors"] == 1 and r.diagnostics["per_item_recall@2"]["q2"] == 0.0


# --- 6. graph / relational -----------------------------------------------------------------------------------------

REL = _gold([("r1", ["A", "B", "C"]), ("r2", ["D"]), ("r3", ["E", "F"])],
            r1={"hops": 1}, r2={"hops": 2}, r3={"hops": 2})


def test_recall_over_the_answer_set_ignores_order_and_breaks_down_by_a_metadata_key():
    reached = {"r1": ["C", "X", "A"], "r2": ["D"], "r3": ["Y"]}
    r = score_relational(REL, reached, recall=0.5, by="hops")
    assert r.gate == {"recall": pytest.approx((2 / 3 + 1 + 0) / 3)} and r.passed is True
    assert r.diagnostics["by_hops"] == {"1": {"n": 1, "recall": pytest.approx(2 / 3)},
                                        "2": {"n": 2, "recall": 0.5}}


def test_over_reach_is_reported_as_precision_never_gated():
    r = score_relational(REL, {"r1": ["A", "B", "C", "X"], "r2": ["D"], "r3": ["E", "F"]}, recall=1.0)
    assert r.passed is True and r.diagnostics["precision"] == pytest.approx(6 / 7)


def test_k_truncates_a_ranked_answer_like_the_reference():
    r = score_relational(REL, {"r1": ["X", "A", "B", "C"], "r2": ["D"], "r3": ["E", "F"]}, recall=0.0, k=2)
    assert r.diagnostics["per_item_recall"]["r1"] == pytest.approx(1 / 3)


def test_misses_name_the_answers_not_reached_and_a_missing_answer_is_an_error():
    r = score_relational(REL, {"r1": ["A", "B"], "r3": ["E", "F"]}, recall=0.9)
    assert r.diagnostics["errors"] == 1 and r.passed is False
    assert r.diagnostics["misses"] == ["r1: C", "r2: D"]
