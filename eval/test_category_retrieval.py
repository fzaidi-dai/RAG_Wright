"""T48 (FR-C.3, RAC-48): tests for the category-label retrieval control arm.

Hermetic: corpus categories, the query->category map, and the queries are built in memory. The control
retrieves by category-label membership alone (no embedding, no ranking), so it measures how much of the lift
coarse label matching captures on its own -- the honest comparison OKF traversal (T50) is judged against.
"""

from __future__ import annotations

from eval.acord import AcordQuery
from eval.category_retrieval import (
    containment,
    evaluate_category_control,
    make_category_retrieve,
    members_by_category,
)

_CORPUS = {"c1": "Governing Law", "c2": "Governing Law", "c3": "Indemnification", "c4": "_uncategorized"}
_TEXT2CAT = {"gov query": "Governing Law", "ind query": "Indemnification", "orphan": "Rare Category"}


def _queries() -> list[AcordQuery]:
    return [
        AcordQuery(query_id="gov query", text="gov query", relevant=frozenset({"c1"}), graded={"c1": 4}),
        AcordQuery(query_id="ind query", text="ind query", relevant=frozenset({"c3"}), graded={"c3": 3}),
    ]


def test_members_inverts_and_excludes_uncategorized():
    members = members_by_category(_CORPUS)
    assert members["Governing Law"] == ["c1", "c2"]  # sorted, deterministic
    assert members["Indemnification"] == ["c3"]
    assert "_uncategorized" not in members  # abstentions are not a retrievable category


def test_retrieve_returns_matched_category_bucket():
    retrieve = make_category_retrieve(_TEXT2CAT, members_by_category(_CORPUS))
    assert retrieve("gov query") == ["c1", "c2"]
    assert retrieve("ind query") == ["c3"]
    assert retrieve("orphan") == []  # category has no clauses -> empty bucket
    assert retrieve("unknown text") == []  # query with no category -> empty


def test_containment_is_the_arms_ceiling():
    # every gold clause is in its query's category bucket -> containment 1.0
    assert containment(_queries(), _TEXT2CAT, members_by_category(_CORPUS)) == 1.0


def test_evaluate_reports_recall_and_ndcg():
    report, contain = evaluate_category_control(_queries(), _TEXT2CAT, _CORPUS)
    assert report.n_queries == 2
    assert report.recall_at_50 == 1.0  # each query's gold sits in its (tiny) category bucket
    assert report.ndcg_at_10 > 0.0
    assert contain == 1.0
