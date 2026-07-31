"""LG-3c: `cross_corpus_retrieval` as a composite LangGraph subgraph.

Retrieve ranked, cited clauses for a query across the whole clause corpus, composing the query-side subgraph +
the three CAP-REG-3 retrieval-core functions into the KG-primary ranking (ADR-0033):

        START
        /    \\                      (the two LLM front-door steps fan out in parallel)
  extract_c   classify_f
      |            |
      |          route          (candidate_routing: union functions -> candidate pool)
      |            |
      |          hydrate        (fetch each candidate's grounded props + dense vector + text)
       \\          /
         rank                   (typed_constraint_match_rank primary, dense_rank_tiebreak within ties)
          |
        assemble                (build the cited, ordered RetrievedClause list)  --> END

- **extract_constraints** (query_constraint_extraction, LG-2a) and **classify_functions** (query function
  classification) are two independent LLM calls, so they run in one parallel superstep (the parallelize-LLM
  rule), writing different state keys -- no reducer needed.
- **route** unions the function predictions and fetches the candidate pool (`candidate_routing`).
- **hydrate** fetches, for the pooled candidates, their grounded typed props (for the match), dense vectors
  (for the tiebreak), and text (for the citation) -- one store+embedder round-trip behind the `hydrate_fn` seam.
- **rank** composes the two pure ranking functions: `typed_constraint_match_rank` is the primary graded order
  (recall-safe), and `dense_rank_tiebreak` re-orders only within equal-match groups (KG-6 / V4).
- **assemble** emits `CrossCorpusRetrieval` -- each clause cited by its `clause_id` with its text and rank.

Query-side posture (matches the other query composites): judicious, graceful degradation. Every I/O node
retries a transient blip then degrades to empty on exhaustion -- empty constraints fall back to a dense-only
order (the query survives), empty functions yield empty results (no fabricated pool). The pure `rank` /
`assemble` nodes never fail. The four seams are dependency-injected for hermetic testing;
`production_cross_corpus_retrieval` wires the real subgraph + capabilities + store + embedder.
"""

from __future__ import annotations

from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from pydantic import BaseModel

from rag_wright.capabilities.retrieval_core import (
    candidate_routing,
    dense_rank_tiebreak,
    typed_constraint_match_rank,
)
from rag_wright.subgraphs.scaffold import DEFAULT_RETRY, business_span
from rag_wright.subgraphs.typed_clause_extraction import TransientExtraction  # shared retryable-blip signal

# The injected seams (the LLM / store / embedder I/O; the pure retrieval-core functions are called directly).
ConstraintsFn = Callable[[str], set]  # query -> typed (dimension, value) constraints (query_constraint_extraction)
FunctionsFn = Callable[[str], list]  # query -> ranked function predictions per router [[...], [...]]
PoolFn = Callable[[list], list]  # functions -> candidate clause ids (store)
# hydrate: (query, candidate_ids) -> (query_vector, {clause_id: {"props": set, "vector": list, "text": str}})
HydrateFn = Callable[[str, list], tuple]


class RetrievedClause(BaseModel):
    """One ranked, cited retrieval result: the clause id (the citation), its text, its constraint-match score,
    and its 1-based rank."""

    clause_id: str
    text: str
    match_score: float
    rank: int


class CrossCorpusRetrieval(BaseModel):
    """The `cross_corpus_retrieval` output: the query and its ranked, cited clauses (FR-Q.6)."""

    query: str
    results: list[RetrievedClause]


class CrossCorpusState(TypedDict, total=False):
    query: str
    constraints: set
    function_predictions: list
    candidate_ids: list
    query_vector: list
    candidates: dict  # {clause_id: {"props": set, "vector": list, "text": str}}
    ranked: list  # [RankedClause] after match + tiebreak composition
    retrieval: CrossCorpusRetrieval


def _degrading_io(name: str, work: Callable[[], dict], empty: dict, runtime: Runtime, max_attempts: int) -> dict:
    """Run an I/O node's `work`: a transient blip re-raises so the RetryPolicy retries, EXCEPT on the final
    attempt where it degrades to `empty` (the query survives -- never a crash)."""
    attempt = runtime.execution_info.node_attempt
    with business_span(name):
        try:
            return work()
        except Exception as exc:  # noqa: BLE001 - transient -> retry, or degrade to empty on exhaustion
            if attempt >= max_attempts:
                return empty
            raise TransientExtraction(str(exc)) from exc


def build_cross_corpus_retrieval(
    constraints_fn: ConstraintsFn,
    functions_fn: FunctionsFn,
    pool_fn: PoolFn,
    hydrate_fn: HydrateFn,
    *,
    retry_policy: Any = DEFAULT_RETRY,
):
    """Compile the `cross_corpus_retrieval` subgraph. The four I/O seams are injected for hermetic testing;
    `retry_policy` is each I/O node's policy (overridable for fast tests)."""
    max_attempts = int(getattr(retry_policy, "max_attempts", 3))

    def extract_constraints(state: CrossCorpusState, runtime: Runtime) -> CrossCorpusState:
        return _degrading_io(
            "cross_corpus_retrieval.extract_constraints",
            lambda: {"constraints": constraints_fn(state["query"])},
            {"constraints": set()}, runtime, max_attempts)

    def classify_functions(state: CrossCorpusState, runtime: Runtime) -> CrossCorpusState:
        return _degrading_io(
            "cross_corpus_retrieval.classify_functions",
            lambda: {"function_predictions": functions_fn(state["query"])},
            {"function_predictions": []}, runtime, max_attempts)

    def route(state: CrossCorpusState, runtime: Runtime) -> CrossCorpusState:
        return _degrading_io(
            "cross_corpus_retrieval.route",
            lambda: {"candidate_ids": candidate_routing(
                state.get("function_predictions", []), pool_fn=pool_fn).candidate_ids},
            {"candidate_ids": []}, runtime, max_attempts)

    def hydrate(state: CrossCorpusState, runtime: Runtime) -> CrossCorpusState:
        candidate_ids = state.get("candidate_ids", [])
        if not candidate_ids:
            return {"query_vector": [], "candidates": {}}

        def work() -> dict:
            query_vector, candidates = hydrate_fn(state["query"], candidate_ids)
            return {"query_vector": query_vector, "candidates": candidates}

        return _degrading_io("cross_corpus_retrieval.hydrate", work,
                             {"query_vector": [], "candidates": {}}, runtime, max_attempts)

    def rank(state: CrossCorpusState) -> CrossCorpusState:
        candidates = state.get("candidates", {})
        constraints = state.get("constraints", set())
        # primary: graded constraint match (recall-safe, stable); tiebreak: cosine only WITHIN equal-match groups
        match = typed_constraint_match_rank(
            constraints, [(cid, candidates[cid]["props"]) for cid in candidates])
        dense = dense_rank_tiebreak(
            state.get("query_vector", []), [(cid, candidates[cid]["vector"]) for cid in candidates])
        dense_pos = {r.clause_id: i for i, r in enumerate(dense.ranked)}
        ranked = sorted(match.ranked, key=lambda r: (-r.match_score, dense_pos.get(r.clause_id, 0)))
        return {"ranked": ranked}

    def assemble(state: CrossCorpusState) -> CrossCorpusState:
        candidates = state.get("candidates", {})
        results = [
            RetrievedClause(
                clause_id=r.clause_id, text=candidates.get(r.clause_id, {}).get("text", ""),
                match_score=r.match_score, rank=i + 1)
            for i, r in enumerate(state.get("ranked", []))
        ]
        return {"retrieval": CrossCorpusRetrieval(query=state["query"], results=results)}

    g = StateGraph(CrossCorpusState)
    g.add_node("extract_constraints", extract_constraints, retry_policy=retry_policy)
    g.add_node("classify_functions", classify_functions, retry_policy=retry_policy)
    g.add_node("route", route, retry_policy=retry_policy)
    g.add_node("hydrate", hydrate, retry_policy=retry_policy)
    g.add_node("rank", rank)
    g.add_node("assemble", assemble)

    g.add_edge(START, "extract_constraints")  # parallel fan-out: the two LLM steps run in one superstep
    g.add_edge(START, "classify_functions")
    g.add_edge("classify_functions", "route")
    g.add_edge("route", "hydrate")
    g.add_edge("extract_constraints", "rank")  # rank joins: waits for constraints AND the hydrate chain
    g.add_edge("hydrate", "rank")
    g.add_edge("rank", "assemble")
    g.add_edge("assemble", END)
    return g.compile()


def production_cross_corpus_retrieval(
    *, pool_fn: PoolFn, hydrate_fn: HydrateFn, extract_model_id: str, function_model_id: str
):
    """Wire the two universal LLM front-door capabilities into the composite: the real
    `query_constraint_extraction` subgraph (LG-2a) for the typed constraints, and `query_function_classification`
    for the routing functions. The `pool_fn` (functions -> candidate clause ids) and `hydrate_fn` (candidates ->
    props + dense vector + text) are supplied by the caller because they are corpus- and identity-specific --
    which store query yields the pool, and how a clause id maps to its grounded props / embedding / text, depend
    on the deployed corpus (the two-halves boundary: the composite is capability-general; corpus binding is the
    integration layer's job, and it holds the store + embedder). Imports are lazy for a hermetic module."""
    from rag_wright.capabilities.query_function_classifier import classify_query_functions
    from rag_wright.subgraphs.query_constraint_extraction import build_query_constraint_extraction
    from rag_wright.subgraphs.typed_clause_extraction import production_record_fn

    qce = build_query_constraint_extraction(production_record_fn(), model_id=extract_model_id)

    def constraints_fn(query: str) -> set:
        out = qce.invoke({"query_text": query, "model_id": extract_model_id})
        return {tuple(c) for c in out.get("constraints", [])}

    def functions_fn(query: str) -> list:
        return [classify_query_functions(query, function_model_id)]  # one router; the union combiner accepts more

    return build_cross_corpus_retrieval(constraints_fn, functions_fn, pool_fn, hydrate_fn)


def register_cross_corpus_retrieval(registry) -> None:
    """LG-3c: register `cross_corpus_retrieval` (composite subgraph; constraints+functions -> routed, graded,
    tie-broken, cited clauses)."""
    registry.register(
        "cross_corpus_retrieval",
        contract=CrossCorpusRetrieval,
        kind="subgraph",
        display_name="Cross-corpus retrieval (ranked, cited clauses)",
    )
