"""LG-2: `query_constraint_extraction` as a hardened LangGraph subgraph.

The query-side counterpart of `typed_clause_extraction` (LG-1): run the SAME granite + `clause_template`
extractor on the QUERY, producing its typed (dimension, value) constraints (KG-5b). Two differences from the
clause subgraph, both deliberate:

  - **no reground / escalation** -- the query IS the source text, and KG-5d found reground-on-query FALSE-FLAGS
    real constraints (e.g. "control the defense" != the clause cue "control of the defense"), so we do not
    ground query constraints; a single extract call is the pattern (KG-5e, granite json_schema);
  - **no dead-letter-drops-the-item** -- a query that yields nothing degrades to an EMPTY constraint set
    (the retrieval falls back to embedding-only ranking), never a dropped item.

Hardening applied JUDICIOUSLY (not every subgraph needs every primitive): a single extract with **graceful
degradation** -- a transient OR genuine failure yields an EMPTY constraint set (the query survives), so there
is no RetryPolicy or dead-letter here. The observability wrap on the raw-SDK (docling-graph/LiteLLM) call is
kept. `record_fn` is injected for hermetic testing.
"""

from __future__ import annotations

from typing import Callable, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from rag_wright.packs.contracts.schemas.property import ClausePropertyRecord
from rag_wright.subgraphs.scaffold import raw_llm_span
from rag_wright.packs.contracts.subgraphs.typed_clause_extraction import TransientExtraction  # shared retryable-blip signal

RecordFn = Callable[[str, str], Optional[ClausePropertyRecord]]


class QueryConstraintState(TypedDict, total=False):
    query_text: str
    model_id: str
    record: Optional[ClausePropertyRecord]
    constraints: list  # [(dimension, value), ...] -- empty when extraction yields nothing


def _constraints_of(record: Optional[ClausePropertyRecord]) -> list:
    if record is None:
        return []
    return [(a.dimension.value, a.value) for a in record.assertions]


def build_query_constraint_extraction(record_fn: RecordFn, *, model_id: str):
    """Compile the `query_constraint_extraction` subgraph. `record_fn` is injected for hermetic testing.
    A genuine extraction failure (record_fn -> None) or a transient failure that exhausts retries degrades to
    an empty constraint set -- the query is never dropped."""

    def extract(state: QueryConstraintState) -> QueryConstraintState:
        model = state.get("model_id", model_id)
        try:
            with raw_llm_span("query_constraint_extraction.extract", model=model):
                record = record_fn(state["query_text"], model)
        except TransientExtraction:
            record = None  # any failure -> empty constraints; the query survives (embedding-only fallback)
        return {"record": record, "constraints": _constraints_of(record)}

    g = StateGraph(QueryConstraintState)
    g.add_node("extract", extract)
    g.add_edge(START, "extract")
    g.add_edge("extract", END)
    return g.compile()


def register_query_constraint_extraction(registry) -> None:
    """LG-2: register `query_constraint_extraction` (subgraph; query-side typed constraint extraction)."""
    registry.register(
        "query_constraint_extraction",
        contract=ClausePropertyRecord,
        kind="subgraph",
        display_name="Query constraint extraction",
    )
