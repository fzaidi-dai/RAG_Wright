"""T33: ACORD retrieval-bar scoring tests (hermetic — an injected retrieve, no store or model).

Pin the metric mechanics against a known ranking: recall@50/@10 at the grade->=2 floor and graded nDCG@10
with exponential gain, plus the ideal/empty edges."""

from __future__ import annotations

from eval.acord import AcordQuery
from eval.acord_retrieval import evaluate_retrieval, ndcg_at_k


def _query() -> AcordQuery:
    # relevant (grade >= 2) = {c1, c2, c3}; c4 grade 1 is non-relevant; graded map keeps all.
    return AcordQuery(
        query_id="q1",
        text="a query",
        relevant=frozenset({"c1", "c2", "c3"}),
        graded={"c1": 4, "c2": 3, "c3": 2, "c4": 1},
    )


def test_ndcg_is_1_for_the_ideal_ranking():
    graded = {"c1": 4, "c2": 3, "c3": 2, "c4": 1}
    ideal = ["c1", "c2", "c3", "c4"]  # grades descending
    assert abs(ndcg_at_k(ideal, graded, 10) - 1.0) < 1e-9


def test_ndcg_is_penalized_when_a_low_grade_is_ranked_first():
    graded = {"c1": 4, "c2": 3, "c3": 2, "c4": 1}
    worse = ["c4", "c3", "c2", "c1"]  # exact reverse of ideal
    assert ndcg_at_k(worse, graded, 10) < ndcg_at_k(["c1", "c2", "c3", "c4"], graded, 10)


def test_recall_gate_counts_only_grade_at_least_floor_clauses():
    q = _query()
    # top-10 has 2 of the 3 relevant (c1, c2); c4 is grade-1 (not counted), plus distractors
    ranked = ["c1", "x", "c2", "x", "c4", "x", "x", "x", "x", "x", "c3"]  # c3 at rank 11
    report = evaluate_retrieval([q], lambda _t: ranked, k_gate=50, k_secondary=10)
    assert report.n_queries == 1
    assert abs(report.recall_at_10 - (2 / 3)) < 1e-9  # c1, c2 in top-10; c3 at 11 excluded
    assert abs(report.recall_at_50 - 1.0) < 1e-9  # all three relevant within top-50
    assert report.per_query_recall_at_50["q1"] == report.recall_at_50


def test_perfect_retrieval_scores_one_across_the_board():
    q = _query()
    ranked = ["c1", "c2", "c3", "c4"]
    report = evaluate_retrieval([q], lambda _t: ranked)
    assert report.recall_at_50 == 1.0
    assert report.recall_at_10 == 1.0
    assert abs(report.ndcg_at_10 - 1.0) < 1e-9
