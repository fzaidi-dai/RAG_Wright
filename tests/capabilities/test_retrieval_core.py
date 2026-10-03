"""CAP-REG-3: the retrieval core packaged out of eval/kg_primary.py as registered `function` capabilities.

Three pure, deterministic functions (the LLM work is upstream in query_constraint_extraction /
query_function_classification): candidate_routing (the union combiner -> pool), typed_constraint_match_rank
(graded rank by KG-5a constraint match, recall-safe), dense_rank_tiebreak (cosine order for meaningful ties).
Hermetic: no store, no LLM, no embedding model -- the store pool lookup is an injected seam, vectors are given.
"""

from __future__ import annotations

from rag_wright.capabilities.retrieval_core import (
    CandidatePool,
    DenseRanking,
    MatchRanking,
    candidate_routing,
    dense_rank_tiebreak,
    typed_constraint_match_rank,
)
from rag_wright.contracts.value_match import constraint_match_count  # the DOMAIN matcher, injected (EP-CORE-1b)

# --- candidate_routing: the union combiner + pool lookup -------------------------------------------------

def test_candidate_routing_unions_predictions_first_wins_then_pools():
    calls = []

    def pool_fn(functions):
        calls.append(list(functions))
        return ["c1", "c2"]

    # two routers (e.g. LLM + classifier); union is first-wins, order-preserving, NONE/empty dropped
    out = candidate_routing([["Cap On Liability", "NONE"], ["Indemnity", "Cap On Liability"]], pool_fn=pool_fn)

    assert isinstance(out, CandidatePool)
    assert out.functions == ["Cap On Liability", "Indemnity"]  # unioned, deduped, NONE dropped, order kept
    assert calls == [["Cap On Liability", "Indemnity"]]  # pool fetched once for the union
    assert out.candidate_ids == ["c1", "c2"]


def test_candidate_routing_empty_predictions_yields_empty_pool_without_store_call():
    called = False

    def pool_fn(functions):
        nonlocal called
        called = True
        return ["x"]

    out = candidate_routing([[], ["NONE"]], pool_fn=pool_fn)

    assert out.functions == [] and out.candidate_ids == []
    assert called is False  # no functions -> no store lookup


# --- typed_constraint_match_rank: graded, recall-safe, subsumption-aware ---------------------------------

def test_match_rank_grades_by_constraint_count_with_canonicalization_and_keeps_zero_match():
    query = {("cap_scope", "mutual"), ("jurisdiction", "england")}
    candidates = [
        ("c1", {("cap_scope", "mutual")}),                                    # 1 match
        ("c2", {("cap_scope", "mutual"), ("jurisdiction", "England and Wales")}),  # 2 (jurisdiction canon.)
        ("c3", set()),                                                        # 0 -- kept, not dropped
    ]

    out = typed_constraint_match_rank(query, candidates, match_count_fn=constraint_match_count)

    assert isinstance(out, MatchRanking)
    assert [(r.clause_id, r.match_score) for r in out.ranked] == [("c2", 2.0), ("c1", 1.0), ("c3", 0.0)]


def test_match_rank_honors_subsumption_rollup():
    query = {("covered_parties", "affiliates")}
    candidates = [("c1", {("covered_parties", "licensor_affiliates")})]  # more specific satisfies broader

    out = typed_constraint_match_rank(query, candidates, match_count_fn=constraint_match_count)

    assert out.ranked[0].match_score == 1.0


def test_match_rank_is_stable_within_a_tie():
    query = {("d", "v")}
    candidates = [("a", set()), ("b", set()), ("c", set())]  # all zero -> input order preserved
    out = typed_constraint_match_rank(query, candidates, match_count_fn=constraint_match_count)
    assert [r.clause_id for r in out.ranked] == ["a", "b", "c"]


# --- dense_rank_tiebreak: cosine order -------------------------------------------------------------------

def test_dense_rank_orders_by_descending_cosine_and_handles_zero_vectors():
    out = dense_rank_tiebreak([1.0, 0.0], [("c1", [1.0, 0.0]), ("c2", [0.0, 1.0]), ("c3", [1.0, 1.0])])

    assert isinstance(out, DenseRanking)
    ids = [r.clause_id for r in out.ranked]
    assert ids == ["c1", "c3", "c2"]  # cos = 1.0, ~0.707, 0.0
    assert out.ranked[0].cosine == 1.0


def test_dense_rank_zero_norm_is_zero_not_error():
    out = dense_rank_tiebreak([0.0, 0.0], [("c1", [1.0, 2.0])])
    assert out.ranked[0].cosine == 0.0


# (EP-CORE-1b/ADR-0118: the three retrieval functions are de-registered from ARD -- generic primitives,
# composed by direct import; their registration test was removed.)


def test_retrieval_core_is_domain_neutral_no_contract_imports():
    """EP-CORE-1b/ADR-0118: retrieval_core is a GENERIC primitive module -- it must import no domain vocab (the
    (dim,value) matcher is injected via `match_count_fn`)."""
    import inspect

    from rag_wright.capabilities import retrieval_core as mod

    src = inspect.getsource(mod)
    assert "from rag_wright.contracts" not in src and "import rag_wright.contracts" not in src
