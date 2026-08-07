"""LEGB-SUBGRAPH (FR-Q, ADR-0033): `typed_property_retrieval` -- the property-boosted Leg B as a hardened
LangGraph subgraph, so the query WORKFLOW is a registered ARD `kind="subgraph"` (the LG-3 declared pattern),
not an imperative script.

A thin composite that wires the registered front-door capabilities + the registered retrieval capability.
Flow: from START, `extract_constraints` (LLM: query -> typed (dim,value) constraints) and `classify_functions`
(LLM + LegalBERT: query -> routed functions) fan out in PARALLEL (one superstep); `retrieve` JOINS both and runs
`property_boosted_retrieval` (BGE pool -> edge.span_id join -> typed_constraint_match_rank -> cited spans);
`assemble` packages the result. START -> {extract_constraints, classify_functions} -> retrieve -> assemble -> END.

Each IO node degrades to EMPTY on exhausted retries (a query survives, never crashes). This is THE corpus-wide
function+property retrieval leg (the redundant `cross_corpus_retrieval`, which used an inferior function-only
pool, was retired in favor of this). The three seams are injected for hermetic testing;
`production_typed_property_retrieval`
wires the real granite/LegalBERT front-door + `property_boosted_retrieval` over the store + A100/local encoders.
`property_boosted_retrieval` stays the registered FUNCTION capability; this subgraph composes it.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from pydantic import BaseModel

from rag_wright.capabilities.property_boosted_retrieval import RankedSpan
from rag_wright.subgraphs.scaffold import DEFAULT_RETRY, business_span
from rag_wright.subgraphs.typed_clause_extraction import TransientExtraction  # shared retryable-blip signal

ConstraintsFn = Callable[[str], set]  # query -> typed (dimension, value) constraints
FunctionsFn = Callable[[str], list]  # query -> routed functions
RetrieveFn = Callable[[str, list, set], list]  # (query, functions, constraints) -> [RankedSpan]


class TypedPropertyRetrieval(BaseModel):
    """The `typed_property_retrieval` output: the query and its property-boosted, cited spans (FR-Q.6)."""

    query: str
    results: list[RankedSpan]


class _State(TypedDict, total=False):
    query: str
    constraints: set
    functions: list
    results: list
    retrieval: TypedPropertyRetrieval


def _degrading_io(name: str, work: Callable[[], dict], empty: dict, runtime: Runtime, max_attempts: int) -> dict:
    """Run an IO node's `work`: a transient blip re-raises so the RetryPolicy retries, EXCEPT on the final
    attempt where it degrades to `empty` (the query survives -- never a crash)."""
    attempt = runtime.execution_info.node_attempt
    with business_span(name):
        try:
            return work()
        except Exception as exc:  # noqa: BLE001 - transient -> retry, or degrade to empty on exhaustion
            if attempt >= max_attempts:
                return empty
            raise TransientExtraction(str(exc)) from exc


def build_typed_property_retrieval(
    constraints_fn: ConstraintsFn,
    functions_fn: FunctionsFn,
    retrieve_fn: RetrieveFn,
    *,
    retry_policy: Any = DEFAULT_RETRY,
):
    """Compile the `typed_property_retrieval` subgraph. The three IO seams are injected for hermetic testing;
    `retry_policy` is each IO node's policy (overridable for fast tests)."""
    max_attempts = int(getattr(retry_policy, "max_attempts", 3))

    def extract_constraints(state: _State, runtime: Runtime) -> _State:
        return _degrading_io("typed_property_retrieval.extract_constraints",
                             lambda: {"constraints": set(constraints_fn(state["query"]))},
                             {"constraints": set()}, runtime, max_attempts)

    def classify_functions(state: _State, runtime: Runtime) -> _State:
        return _degrading_io("typed_property_retrieval.classify_functions",
                             lambda: {"functions": list(functions_fn(state["query"]))},
                             {"functions": []}, runtime, max_attempts)

    def retrieve(state: _State, runtime: Runtime) -> _State:
        return _degrading_io(
            "typed_property_retrieval.retrieve",
            lambda: {"results": retrieve_fn(state["query"], state.get("functions", []),
                                            state.get("constraints", set()))},
            {"results": []}, runtime, max_attempts)

    def assemble(state: _State) -> _State:
        return {"retrieval": TypedPropertyRetrieval(query=state["query"], results=state.get("results", []))}

    g = StateGraph(_State)
    g.add_node("extract_constraints", extract_constraints, retry_policy=retry_policy)
    g.add_node("classify_functions", classify_functions, retry_policy=retry_policy)
    g.add_node("retrieve", retrieve, retry_policy=retry_policy)
    g.add_node("assemble", assemble)
    g.add_edge(START, "extract_constraints")  # parallel fan-out: the two front-door LLM steps in one superstep
    g.add_edge(START, "classify_functions")
    g.add_edge("extract_constraints", "retrieve")  # retrieve joins: waits for BOTH constraints and functions
    g.add_edge("classify_functions", "retrieve")
    g.add_edge("retrieve", "assemble")
    g.add_edge("assemble", END)
    return g.compile()


def production_typed_property_retrieval(
    *, store: Any, embedder: Any, classifier: Any, extract_model: Any,
    function_model_id: str, k: int = 8, pool_k: int = 30, function_topk: int = 3,
):
    """Wire the real Leg B: granite constraint-extraction + granite/LegalBERT routing + the
    `property_boosted_retrieval` capability over the store + encoders (local or the A100 adapters)."""
    from rag_wright.capabilities.dg_extraction import extract_clause
    from rag_wright.capabilities.property_boosted_retrieval import property_boosted_retrieval
    from rag_wright.capabilities.query_function_classifier import classify_query_functions
    from rag_wright.contracts.function import NO_FUNCTION
    from rag_wright.contracts.identifiers import ChunkId
    from rag_wright.spans.clause_kg_extractor import clause_to_record

    def constraints_fn(query: str) -> set:
        clause = extract_clause(query, extract_model)
        if clause is None:
            return set()
        # a QUERY has no clause function -> the NO_FUNCTION sentinel (only the extracted properties are used).
        # Was a hardcoded "Cap On Liability" hack to pass ClausePropertyRecord validation -- mislabeled every query.
        rec = clause_to_record(clause, chunk_id=ChunkId.of("q", 0, query), function=NO_FUNCTION)
        return {(a.dimension.value, a.value) for a in rec.assertions}

    def functions_fn(query: str) -> list:
        llm = classify_query_functions(query, function_model_id, k=function_topk)
        lb = classifier.classify_topk([query], k=function_topk)[0]
        return list(dict.fromkeys([*llm, *lb]))  # KG-5e llm-union

    def retrieve_fn(query: str, functions: Iterable[str], constraints: set) -> list:
        return property_boosted_retrieval(
            query, store=store, embedder=embedder, functions=functions, constraints=constraints,
            k=k, pool_k=pool_k)

    return build_typed_property_retrieval(constraints_fn, functions_fn, retrieve_fn)


def register_typed_property_retrieval(registry) -> None:
    """LEGB-SUBGRAPH: register `typed_property_retrieval` (composite subgraph; front-door + property-boost rerank)."""
    registry.register(
        "typed_property_retrieval",
        contract=TypedPropertyRetrieval,
        kind="subgraph",
        display_name="Typed property-boosted retrieval (Leg B)",
    )
