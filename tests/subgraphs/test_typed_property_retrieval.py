"""LEGB-SUBGRAPH (ADR-0033): the `typed_property_retrieval` composite subgraph. Hermetic -- injected seams,
no LLM / store / embedder. The front-door (constraints, functions) fans out in parallel; `retrieve` joins both
and calls the property_boosted_retrieval seam; a transient failure degrades to empty (never a crash)."""

from __future__ import annotations

from langgraph.types import RetryPolicy

from rag_wright.capabilities.property_boosted_retrieval import RankedSpan
from rag_wright.subgraphs.typed_property_retrieval import (
    TypedPropertyRetrieval,
    build_typed_property_retrieval,
)

_FAST_RETRY = RetryPolicy(max_attempts=3, initial_interval=0.0)


def _span(sid, match_score=1.0):
    return RankedSpan(span_id=sid, text=f"text-{sid}", function="Anti-Assignment",
                      match_score=match_score, matched=[("assignment_consent", "free")], rank=1)


def _seams(*, constraints, functions, results, fail_constraints=0, fail_functions=0, fail_retrieve=0):
    calls = {"constraints": 0, "functions": 0, "retrieve": 0}
    seen: dict = {}

    def constraints_fn(query):
        calls["constraints"] += 1
        if calls["constraints"] <= fail_constraints:
            raise RuntimeError("constraint blip")
        return set(constraints)

    def functions_fn(query):
        calls["functions"] += 1
        if calls["functions"] <= fail_functions:
            raise RuntimeError("function blip")
        return list(functions)

    def retrieve_fn(query, fns, cons):
        calls["retrieve"] += 1
        seen["functions"], seen["constraints"] = list(fns), set(cons)
        if calls["retrieve"] <= fail_retrieve:
            raise RuntimeError("retrieve blip")
        return list(results)

    return (constraints_fn, functions_fn, retrieve_fn), calls, seen


def _run(seams):
    g = build_typed_property_retrieval(*seams, retry_policy=_FAST_RETRY)
    return g.invoke({"query": "anti-assignment freely assignable"})["retrieval"]


def test_happy_path_produces_cited_results():
    seams, _, _ = _seams(constraints={("assignment_consent", "free")}, functions=["Anti-Assignment"],
                         results=[_span("s3"), _span("s1", 0.0)])
    out = _run(seams)
    assert isinstance(out, TypedPropertyRetrieval)
    assert [r.span_id for r in out.results] == ["s3", "s1"]
    assert out.query == "anti-assignment freely assignable"


def test_retrieve_joins_both_constraints_and_functions():
    seams, _, seen = _seams(constraints={("assignment_consent", "free")}, functions=["Anti-Assignment"],
                            results=[_span("s3")])
    _run(seams)
    assert seen["constraints"] == {("assignment_consent", "free")}
    assert seen["functions"] == ["Anti-Assignment"]


def test_constraints_failure_degrades_to_empty_but_retrieval_proceeds():
    # constraints fail all attempts -> empty set; retrieve still runs (functions-only pool, no boost)
    seams, calls, seen = _seams(constraints={("x", "y")}, functions=["Anti-Assignment"],
                                results=[_span("s1", 0.0)], fail_constraints=99)
    out = _run(seams)
    assert seen["constraints"] == set()  # degraded to empty
    assert [r.span_id for r in out.results] == ["s1"]


def test_functions_failure_degrades_to_empty():
    seams, _, seen = _seams(constraints={("assignment_consent", "free")}, functions=["Anti-Assignment"],
                            results=[], fail_functions=99)
    _run(seams)
    assert seen["functions"] == []


def test_retrieve_failure_degrades_to_empty_results_never_crashes():
    seams, _, _ = _seams(constraints={("assignment_consent", "free")}, functions=["Anti-Assignment"],
                         results=[_span("s3")], fail_retrieve=99)
    out = _run(seams)
    assert out.results == []  # empty, but a valid result -- no crash
