"""LG-3c: the `cross_corpus_retrieval` composite subgraph -- hermetic (stub seams, no LLM/store/embedder).

Composes query_constraint_extraction (LG-2a) + query function classification with the three CAP-REG-3
retrieval-core functions (candidate_routing -> typed_constraint_match_rank -> dense_rank_tiebreak) into one
ranked-cited-clauses pipeline. The two LLM steps (constraints, functions) fan out in parallel. Query-side
posture: any transient failure degrades to empty (empty constraints -> dense-only order; empty functions ->
empty results), never a crash.
"""

from __future__ import annotations

from langgraph.types import RetryPolicy

from rag_wright.subgraphs.cross_corpus_retrieval import (
    CrossCorpusRetrieval,
    build_cross_corpus_retrieval,
)

_FAST_RETRY = RetryPolicy(max_attempts=3, initial_interval=0.0)


def _seams(*, constraints, functions, pool, hydrate, fail_constraints=0, fail_route=0):
    """Build the four injected seams with call counters and optional failures."""
    calls = {"constraints": 0, "functions": 0, "route": 0, "hydrate": 0}

    def constraints_fn(query):
        calls["constraints"] += 1
        if calls["constraints"] <= fail_constraints:
            raise RuntimeError("extract blip")
        return set(constraints)

    def functions_fn(query):
        calls["functions"] += 1
        return [list(f) for f in functions]

    def pool_fn(fns):
        calls["route"] += 1
        if calls["route"] <= fail_route:
            raise RuntimeError("pool blip")
        return list(pool)

    def hydrate_fn(query, candidate_ids):
        calls["hydrate"] += 1
        return [1.0, 0.0], {cid: hydrate[cid] for cid in candidate_ids if cid in hydrate}

    return constraints_fn, functions_fn, pool_fn, hydrate_fn, calls


def test_composes_pipeline_into_ranked_cited_clauses():
    cf, ff, pf, hf, calls = _seams(
        constraints={("cap_scope", "mutual")},
        functions=[["Cap On Liability"]],
        pool=["c1", "c2"],
        hydrate={
            "c1": {"props": {("cap_scope", "mutual")}, "vector": [0.0, 1.0], "text": "Mutual cap clause."},
            "c2": {"props": set(), "vector": [1.0, 0.0], "text": "Unrelated clause."},
        },
    )
    graph = build_cross_corpus_retrieval(cf, ff, pf, hf, retry_policy=_FAST_RETRY)

    out = graph.invoke({"query": "mutual liability cap"})
    ret = out["retrieval"]

    assert isinstance(ret, CrossCorpusRetrieval)
    ids = [r.clause_id for r in ret.results]
    assert ids == ["c1", "c2"]  # c1 matches the constraint (score 1) -> ranks above c2 (score 0)
    assert ret.results[0].match_score == 1.0 and ret.results[0].rank == 1
    assert ret.results[0].text == "Mutual cap clause."  # cited with its text
    assert calls["constraints"] == 1 and calls["functions"] == 1


def test_dense_tiebreak_orders_within_equal_match():
    # both candidates match the single constraint (score 1) -> the embedding cosine breaks the tie
    cf, ff, pf, hf, _ = _seams(
        constraints={("d", "v")},
        functions=[["F"]],
        pool=["a", "b"],
        hydrate={
            "a": {"props": {("d", "v")}, "vector": [0.2, 1.0], "text": "a"},   # lower cosine to [1,0]
            "b": {"props": {("d", "v")}, "vector": [1.0, 0.0], "text": "b"},   # higher cosine to [1,0]
        },
    )
    graph = build_cross_corpus_retrieval(cf, ff, pf, hf, retry_policy=_FAST_RETRY)

    out = graph.invoke({"query": "q"})  # query_vector = [1,0] from the stub hydrate
    assert [r.clause_id for r in out["retrieval"].results] == ["b", "a"]  # b wins the tie on cosine


def test_empty_functions_yield_empty_results():
    cf, ff, pf, hf, calls = _seams(constraints={("d", "v")}, functions=[], pool=["x"], hydrate={})
    graph = build_cross_corpus_retrieval(cf, ff, pf, hf, retry_policy=_FAST_RETRY)

    out = graph.invoke({"query": "q"})
    assert out["retrieval"].results == []
    assert calls["route"] == 0  # no functions -> candidate_routing short-circuits, no pool lookup


def test_constraints_failure_degrades_to_dense_only_ranking():
    # constraints_fn always fails -> empty constraints -> every match score 0 -> order is pure dense cosine
    cf, ff, pf, hf, calls = _seams(
        constraints={("d", "v")},
        functions=[["F"]],
        pool=["a", "b"],
        hydrate={
            "a": {"props": {("d", "v")}, "vector": [0.2, 1.0], "text": "a"},
            "b": {"props": {("d", "v")}, "vector": [1.0, 0.0], "text": "b"},
        },
        fail_constraints=99,
    )
    graph = build_cross_corpus_retrieval(cf, ff, pf, hf, retry_policy=_FAST_RETRY)

    out = graph.invoke({"query": "q"})
    results = out["retrieval"].results
    assert calls["constraints"] == 3  # retried up to max_attempts, then degraded to empty
    assert [r.match_score for r in results] == [0.0, 0.0]  # no constraints matched
    assert [r.clause_id for r in results] == ["b", "a"]  # dense-only order (query survives)


def test_transient_route_retries_then_degrades_to_empty():
    cf, ff, pf, hf, calls = _seams(
        constraints={("d", "v")}, functions=[["F"]], pool=["a"], hydrate={}, fail_route=99)
    graph = build_cross_corpus_retrieval(cf, ff, pf, hf, retry_policy=_FAST_RETRY)

    out = graph.invoke({"query": "q"})
    assert calls["route"] == 3  # retried up to max_attempts
    assert out["retrieval"].results == []  # degraded to empty pool -> empty results


def test_registers_as_a_subgraph():
    from rag_wright.capabilities.registry import CapabilityRegistry
    from rag_wright.subgraphs.cross_corpus_retrieval import register_cross_corpus_retrieval

    reg = CapabilityRegistry()
    register_cross_corpus_retrieval(reg)
    assert reg.get("cross_corpus_retrieval").kind == "subgraph"
    assert reg.get("cross_corpus_retrieval").contract is CrossCorpusRetrieval
