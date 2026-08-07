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


# --- A3: the contract-KG corpus binding (pool_fn / hydrate_fn) + the two new store reads ---------------------


class _FakeContractStore:
    """Implements only the store surface the A3 bindings use."""

    def spans_by_functions(self, functions, *, limit=200):
        return [{"span_id": f"s-{f}", "function": f} for f in functions][:limit]

    def span_properties(self, ids):
        return {sid: {("cap_basis", "multiple_of_fees")} for sid in ids}

    def span_vectors_by_id(self, ids):
        return {sid: [0.1, 0.2] for sid in ids}

    def span_texts(self, ids):
        return {sid: f"text of {sid}" for sid in ids}


class _FakeEmbedder:
    def encode_dense(self, query):
        return [1.0, 0.0]


def test_contract_pool_fn_returns_corpus_wide_span_ids_for_the_routed_functions():
    from rag_wright.subgraphs.cross_corpus_retrieval import contract_pool_fn

    pool = contract_pool_fn(_FakeContractStore(), pool_limit=50)
    assert pool(["Cap On Liability", "Anti-Assignment"]) == ["s-Cap On Liability", "s-Anti-Assignment"]


def test_contract_hydrate_fn_composes_props_vector_and_text_per_candidate():
    from rag_wright.subgraphs.cross_corpus_retrieval import contract_hydrate_fn

    qv, candidates = contract_hydrate_fn(_FakeContractStore(), _FakeEmbedder())("liability cap", ["s-1", "s-2"])
    assert qv == [1.0, 0.0]  # the query's dense vector (BGE via the embedder)
    assert candidates["s-1"] == {"props": {("cap_basis", "multiple_of_fees")}, "vector": [0.1, 0.2],
                                 "text": "text of s-1"}


def test_cross_corpus_over_the_contract_bindings_ranks_and_cites():
    # the real contract bindings over a fake store, with stubbed LLM front-doors -> a ranked, cited result
    from rag_wright.subgraphs.cross_corpus_retrieval import (
        build_cross_corpus_retrieval,
        contract_hydrate_fn,
        contract_pool_fn,
    )

    store = _FakeContractStore()
    graph = build_cross_corpus_retrieval(
        constraints_fn=lambda q: {("cap_basis", "multiple_of_fees")},
        functions_fn=lambda q: [["Cap On Liability"]],
        pool_fn=contract_pool_fn(store), hydrate_fn=contract_hydrate_fn(store, _FakeEmbedder()))
    results = graph.invoke({"query": "how is liability capped"})["retrieval"].results
    assert results and results[0].clause_id == "s-Cap On Liability"
    assert results[0].text == "text of s-Cap On Liability" and results[0].match_score >= 1  # matched the constraint


def test_new_store_reads_short_circuit_on_empty_and_shape_the_sql():
    from rag_wright.store.arcadedb import ArcadeDBStore

    store = ArcadeDBStore.__new__(ArcadeDBStore)  # bypass the client connection; stub _query
    captured = {}
    store._query = lambda sql: (captured.__setitem__("sql", sql), [{"span_id": "s1", "function": "F", "dense": [0.5]}])[1]
    assert store.spans_by_functions([]) == [] and store.span_vectors_by_id([]) == {}  # empty short-circuits
    store.spans_by_functions(["Cap On Liability"], limit=10)
    assert "WHERE function IN" in captured["sql"] and "LIMIT 10" in captured["sql"]
    assert store.span_vectors_by_id(["s1"]) == {"s1": [0.5]}  # {span_id: dense}
