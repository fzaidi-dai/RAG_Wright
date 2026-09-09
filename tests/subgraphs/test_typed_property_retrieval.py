"""LEGB-SUBGRAPH (ADR-0033, ADR-0047, issue 0023): the `typed_property_retrieval` composite subgraph. Hermetic --
injected seams, no LLM / store / embedder. The graph is START -> extract_constraints -> retrieve -> judge_relevance
-> assemble. `retrieve` runs over the WHOLE-INDEX pool + property boost; `judge_relevance` (issue 0023) attaches a
per-span relevance verdict when a judge is wired. A transient failure degrades gracefully (never a crash).
Results are `JudgedSpan` (span + relevance verdict; relevance None when no judge is wired)."""

from __future__ import annotations

from langgraph.types import RetryPolicy

from rag_wright.capabilities.property_boosted_retrieval import RankedSpan
from rag_wright.capabilities.span_relevance_judgment import RelevanceVerdict
from rag_wright.subgraphs.typed_property_retrieval import (
    TypedPropertyRetrieval,
    aquery_constraints,
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


async def _run(seams, *, relevance_judge=None, extra_input=None):
    g = build_typed_property_retrieval(*seams, relevance_judge=relevance_judge, retry_policy=_FAST_RETRY)
    out = await g.ainvoke({"query": "anti-assignment freely assignable", **(extra_input or {})})
    return out["retrieval"]


async def test_happy_path_produces_cited_results():
    seams, _, _ = _seams(constraints={("assignment_consent", "free")},
                         results=[_span("s3"), _span("s1", 0.0)])
    out = await _run(seams)
    assert isinstance(out, TypedPropertyRetrieval)
    assert [j.span.span_id for j in out.results] == ["s3", "s1"]
    assert out.query == "anti-assignment freely assignable"
    assert all(j.relevance is None for j in out.results)  # no judge wired -> unjudged (distinct from a verdict)


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
    assert [j.span.span_id for j in out.results] == ["s1"]


async def test_retrieve_failure_degrades_to_empty_results_never_crashes():
    seams, _, _ = _seams(constraints={("assignment_consent", "free")}, results=[_span("s3")], fail_retrieve=99)
    out = await _run(seams)
    assert out.results == []  # empty, but a valid result -- no crash


# --- issue 0023: per-span relevance verdict (judge wired) -----------------------------------------


async def test_judge_attaches_a_verdict_to_every_span_when_wired():
    seams, _, seen = _seams(constraints=set(), results=[_span("s1", 0.0), _span("s2", 0.0)])

    async def judge(spans, condition):
        seen["condition"] = condition  # capture the structured condition the node built
        # s1 relevant, s2 not_relevant -> the product can now reach not_found when nothing is relevant
        return [RelevanceVerdict(verdict="relevant", rationale="on point", confidence=0.9),
                RelevanceVerdict(verdict="not_relevant", rationale="off topic", confidence=0.8)]

    out = await _run(seams, relevance_judge=judge,
                     extra_input={"clause_type": "Anti-Assignment", "value_condition": "freely assignable"})
    assert [j.relevance.verdict for j in out.results] == ["relevant", "not_relevant"]  # every span judged
    assert seen["condition"].clause_type == "Anti-Assignment"
    assert seen["condition"].value_condition == "freely assignable"
    assert seen["condition"].question == "anti-assignment freely assignable"  # the query rides as context


async def test_judge_maps_unreadable_verdict_to_uncertain_conservatively():
    seams, _, _ = _seams(constraints=set(), results=[_span("s1", 0.0)])

    async def judge(spans, condition):
        return [RelevanceVerdict(verdict="probably yes?", rationale="", confidence=2.0)]  # off-vocab + bad confidence

    out = await _run(seams, relevance_judge=judge, extra_input={"clause_type": "Anti-Assignment"})
    v = out.results[0].relevance
    assert v.verdict == "uncertain" and v.confidence == 1.0  # unreadable -> uncertain; confidence clamped


async def test_judge_failure_degrades_to_uncertain_not_none():
    # a wired judge that fails all attempts still gives every span a verdict (uncertain), never None
    seams, _, _ = _seams(constraints=set(), results=[_span("s1", 0.0)])

    async def judge(spans, condition):
        raise RuntimeError("judge blip")

    out = await _run(seams, relevance_judge=judge, extra_input={"clause_type": "Anti-Assignment"})
    assert out.results[0].relevance is not None and out.results[0].relevance.verdict == "uncertain"


async def test_judge_skipped_when_no_clause_type_even_if_wired():
    seams, _, _ = _seams(constraints=set(), results=[_span("s1", 0.0)])

    async def judge(spans, condition):  # must not be called without a condition to judge against
        raise AssertionError("judge ran without a clause_type")

    out = await _run(seams, relevance_judge=judge)  # no clause_type in input
    assert out.results[0].relevance is None  # unjudged (no condition), not a crash


# --- issue 0020: query constraint extraction via client-side tag-parse (ADR-0045), same Clause -> (dim,value) ---


def _tagparse_stub(clause):
    """A build_tag_structured stand-in: ignores model/schema, returns a runnable whose .ainvoke yields `clause`
    (or raises if `clause` is an Exception) -- so aquery_constraints is tested with no LLM/network."""

    class _R:
        async def ainvoke(self, _prompt):
            if isinstance(clause, Exception):
                raise clause
            return clause

    return lambda _m, _s, **_kw: _R()


async def test_aquery_constraints_maps_a_tagparsed_clause_to_dim_value_pairs():
    from rag_wright.ontology.clause_template import CapBasis, CapConstraint, Clause

    clause = Clause(caps=CapConstraint(cap_basis=CapBasis.MULTIPLE_OF_FEES))
    cons = await aquery_constraints("cap at a multiple of fees", "qwen/qwen3.8-27b",
                                    structured_factory=_tagparse_stub(clause))
    assert cons == {("cap_basis", "multiple_of_fees")}  # same (dim,value) mapping as the ingestion path


async def test_aquery_constraints_degrades_to_empty_on_parse_failure():
    # a persistent tag-parse failure -> no constraints (retrieval still runs over the whole-index pool), never a crash
    cons = await aquery_constraints("x", "qwen/qwen3.8-27b",
                                    structured_factory=_tagparse_stub(RuntimeError("parse blip")))
    assert cons == set()


async def test_aquery_constraints_empty_clause_yields_no_constraints():
    from rag_wright.ontology.clause_template import Clause

    cons = await aquery_constraints("hello", "qwen/qwen3.8-27b", structured_factory=_tagparse_stub(Clause()))
    assert cons == set()  # a query mentioning no property -> empty (all fields defaulted/absent)
