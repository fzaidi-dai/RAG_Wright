"""LEGB-SUBGRAPH (ADR-0033, ADR-0047): the `typed_property_retrieval` composite subgraph. Hermetic -- injected
seams, no LLM / store / embedder. ADR-0047 RETIRED the function pre-filter: the graph is
START -> extract_constraints -> retrieve -> assemble (no `classify_functions` node); `retrieve` runs over the
WHOLE-INDEX pool + property boost. A transient failure degrades to empty (never a crash)."""

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


def _seams(*, constraints, results, fail_constraints=0, fail_retrieve=0):
    calls = {"constraints": 0, "retrieve": 0}
    seen: dict = {}

    async def constraints_fn(query):
        calls["constraints"] += 1
        if calls["constraints"] <= fail_constraints:
            raise RuntimeError("constraint blip")
        return set(constraints)

    def retrieve_fn(query, cons):  # ADR-0047: no functions arg -- whole-index pool
        calls["retrieve"] += 1
        seen["constraints"] = set(cons)
        if calls["retrieve"] <= fail_retrieve:
            raise RuntimeError("retrieve blip")
        return list(results)

    return (constraints_fn, retrieve_fn), calls, seen


async def _run(seams):
    g = build_typed_property_retrieval(*seams, retry_policy=_FAST_RETRY)
    out = await g.ainvoke({"query": "anti-assignment freely assignable"})
    return out["retrieval"]


async def test_happy_path_produces_cited_results():
    seams, _, _ = _seams(constraints={("assignment_consent", "free")},
                         results=[_span("s3"), _span("s1", 0.0)])
    out = await _run(seams)
    assert isinstance(out, TypedPropertyRetrieval)
    assert [r.span_id for r in out.results] == ["s3", "s1"]
    assert out.query == "anti-assignment freely assignable"


async def test_retrieve_receives_constraints_only():
    # ADR-0047: retrieve takes (query, constraints) -- no function filter (whole-index pool)
    seams, _, seen = _seams(constraints={("assignment_consent", "free")}, results=[_span("s3")])
    await _run(seams)
    assert seen["constraints"] == {("assignment_consent", "free")}


async def test_constraints_failure_degrades_to_empty_but_retrieval_proceeds():
    # constraints fail all attempts -> empty set; retrieve still runs (whole-index pool, no boost)
    seams, _, seen = _seams(constraints={("x", "y")}, results=[_span("s1", 0.0)], fail_constraints=99)
    out = await _run(seams)
    assert seen["constraints"] == set()  # degraded to empty
    assert [r.span_id for r in out.results] == ["s1"]


async def test_retrieve_failure_degrades_to_empty_results_never_crashes():
    seams, _, _ = _seams(constraints={("assignment_consent", "free")}, results=[_span("s3")], fail_retrieve=99)
    out = await _run(seams)
    assert out.results == []  # empty, but a valid result -- no crash
