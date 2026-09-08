"""LEGB-SUBGRAPH (FR-Q, ADR-0033): `typed_property_retrieval` -- the property-boosted Leg B as a hardened
LangGraph subgraph, so the query WORKFLOW is a registered ARD `kind="subgraph"` (the LG-3 declared pattern),
not an imperative script.

A thin composite that wires the registered front-door capability + the registered retrieval capability.
Flow: `extract_constraints` (LLM: query -> typed (dim,value) constraints) -> `retrieve` (runs
`property_boosted_retrieval` over the WHOLE-INDEX BGE pool -> edge.span_id join -> typed_constraint_match_rank ->
cited spans) -> `assemble`. START -> extract_constraints -> retrieve -> assemble -> END.

ADR-0047: the precomputed clause-function pre-filter was RETIRED (graded-recall showed ON~=OFF, ceiling 0.969,
and the gate is how a mislabel corrupts retrieval). The `classify_functions` node is gone; retrieval is over the
whole index, ranked by property boost + BGE, so a mislabeled clause simply doesn't rank rather than polluting a
filtered pool. Each IO node degrades to EMPTY on exhausted retries (a query survives, never crashes). The two
seams are injected for hermetic testing; `production_typed_property_retrieval` wires the real granite front-door +
`property_boosted_retrieval` over the store + A100/local encoders. `property_boosted_retrieval` stays the
registered FUNCTION capability; this subgraph composes it.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from pydantic import BaseModel

from rag_wright.capabilities.property_boosted_retrieval import RankedSpan
from rag_wright.subgraphs.scaffold import DEFAULT_RETRY, business_span
from rag_wright.subgraphs.typed_clause_extraction import TransientExtraction  # shared retryable-blip signal

# ASYNC-C1 (ADR-0057): constraints_fn is async (its model call gets a true wall-clock deadline via aextract_clause).
ConstraintsFn = Callable[[str], Awaitable[set]]  # query -> typed (dimension, value) constraints
RetrieveFn = Callable[[str, set], list]  # (query, constraints) -> [RankedSpan]  (ADR-0047: whole-index pool)


class TypedPropertyRetrieval(BaseModel):
    """The `typed_property_retrieval` output: the query and its property-boosted, cited spans (FR-Q.6)."""

    query: str
    results: list[RankedSpan]


class _State(TypedDict, total=False):
    query: str
    constraints: set
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


async def _adegrading_io(
    name: str, awork: Callable[[], Awaitable[dict]], empty: dict, runtime: Runtime, max_attempts: int
) -> dict:
    """ASYNC-C1 (ADR-0057): the async twin of `_degrading_io` for a model-calling node -- awaits `awork` (so the
    model call gets its true wall-clock deadline), same transient-retry / degrade-to-empty posture."""
    attempt = runtime.execution_info.node_attempt
    with business_span(name):
        try:
            return await awork()
        except Exception as exc:  # noqa: BLE001 - transient -> retry, or degrade to empty on exhaustion
            if attempt >= max_attempts:
                return empty
            raise TransientExtraction(str(exc)) from exc


def build_typed_property_retrieval(
    constraints_fn: ConstraintsFn,
    retrieve_fn: RetrieveFn,
    *,
    retry_policy: Any = DEFAULT_RETRY,
):
    """Compile the `typed_property_retrieval` subgraph. The two IO seams are injected for hermetic testing;
    `retry_policy` is each IO node's policy (overridable for fast tests). ADR-0047: no function pre-filter --
    `retrieve` runs over the whole-index pool with the property boost."""
    max_attempts = int(getattr(retry_policy, "max_attempts", 3))

    async def extract_constraints(state: _State, runtime: Runtime) -> _State:
        async def _work() -> dict:
            return {"constraints": set(await constraints_fn(state["query"]))}

        return await _adegrading_io("typed_property_retrieval.extract_constraints", _work,
                                    {"constraints": set()}, runtime, max_attempts)

    def retrieve(state: _State, runtime: Runtime) -> _State:
        return _degrading_io(
            "typed_property_retrieval.retrieve",
            lambda: {"results": retrieve_fn(state["query"], state.get("constraints", set()))},
            {"results": []}, runtime, max_attempts)

    def assemble(state: _State) -> _State:
        return {"retrieval": TypedPropertyRetrieval(query=state["query"], results=state.get("results", []))}

    g = StateGraph(_State)
    g.add_node("extract_constraints", extract_constraints, retry_policy=retry_policy)
    g.add_node("retrieve", retrieve, retry_policy=retry_policy)
    g.add_node("assemble", assemble)
    g.add_edge(START, "extract_constraints")
    g.add_edge("extract_constraints", "retrieve")
    g.add_edge("retrieve", "assemble")
    g.add_edge("assemble", END)
    return g.compile()


def production_typed_property_retrieval(
    *, store: Any, embedder: Any, extract_model: Any, k: int = 8, pool_k: int = 30,
):
    """Wire the real Leg B: granite constraint-extraction + the `property_boosted_retrieval` capability over the
    store + encoders (local or the A100 adapters). ADR-0047: no function classifier -- the pool is whole-index."""
    from rag_wright.capabilities.dg_extraction import aextract_clause
    from rag_wright.capabilities.property_boosted_retrieval import property_boosted_retrieval
    from rag_wright.contracts.function import NO_FUNCTION
    from rag_wright.contracts.identifiers import ChunkId
    from rag_wright.spans.clause_kg_extractor import clause_to_record

    async def constraints_fn(query: str) -> set:
        # issue 0019: gleaning=False -- a user query is short and has nothing to "glean" in a second pass; the
        # completeness call returned empty and doubled query cost + latency. Ingestion keeps gleaning (default True).
        clause = await aextract_clause(query, extract_model, gleaning=False)
        if clause is None:
            return set()
        # a QUERY has no clause function -> the NO_FUNCTION sentinel (only the extracted properties are used).
        # Was a hardcoded "Cap On Liability" hack to pass ClausePropertyRecord validation -- mislabeled every query.
        rec = clause_to_record(clause, chunk_id=ChunkId.of("q", 0, query), function=NO_FUNCTION)
        return {(a.dimension.value, a.value) for a in rec.assertions}

    def retrieve_fn(query: str, constraints: set) -> list:
        # ADR-0047: functions=() -> property_boosted_retrieval runs over the WHOLE-INDEX BGE pool (no gate).
        return property_boosted_retrieval(
            query, store=store, embedder=embedder, functions=(), constraints=constraints, k=k, pool_k=pool_k)

    return build_typed_property_retrieval(constraints_fn, retrieve_fn)


def register_typed_property_retrieval(registry) -> None:
    """LEGB-SUBGRAPH: register `typed_property_retrieval` (composite subgraph; front-door + property-boost rerank)."""
    registry.register(
        "typed_property_retrieval",
        contract=TypedPropertyRetrieval,
        kind="subgraph",
        display_name="Typed property-boosted retrieval (Leg B)",
    )
