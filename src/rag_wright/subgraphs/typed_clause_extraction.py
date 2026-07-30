"""LG-1: `typed_clause_extraction` as a hardened LangGraph subgraph -- the REFERENCE implementation.

Wraps the procedural extract -> adapt -> reground (DGClausePropertyExtractor) as a compiled `StateGraph` on
the LG-0 scaffold, with the standard hardening:

    extract_cheap [RetryPolicy]  --dead_letter-->  END
          |  (escalate? & not escalated)     |  (grounded enough)
          v                                  v
     extract_strong (best-effort)  ->  gate [optional HITL]  ->  END

- **extract_cheap** retries transient failures (a custom `TransientExtraction` -> DEFAULT_RETRY retries it,
  since LangGraph's default retry_on excludes ValueError/OSError/... but not custom exceptions). A genuine
  "no models" surfaces as `record_fn -> None` and dead-letters (no wasted retries).
- **escalation** (ADR-0028 Flash->Pro) is a conditional edge on `escalate_fn` (the grounding judge's
  `needs_escalation`), bounded by `escalated`. Strong extraction is best-effort: on any failure it keeps the
  cheap record rather than dead-lettering.
- **dead-letter** terminal: one bad clause is dropped with a reason (never raised), so a batch survives.
- **HITL** (optional): a low-confidence record pauses on `interrupt()` for human review (needs a checkpointer).
- **observability**: the extraction is a raw-SDK call (docling-graph / LiteLLM bypasses LangChain), so it is
  wrapped in `raw_llm_span` per the GraphWright contract; everything else nests automatically.

`record_fn` and `escalate_fn` are dependency-injected so the graph is hermetically testable with fakes -- no
live LLM. `production_record_fn` wires the real `extract_clause` + `clause_to_record`.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from rag_wright.contracts.property import ClausePropertyRecord
from rag_wright.spans.property_grounding import needs_escalation, reground
from rag_wright.subgraphs.scaffold import DEFAULT_RETRY, dead_letter, raw_llm_span

# record_fn: (clause_text, model_id) -> an ADAPTED ClausePropertyRecord (pre-grounding), or None when
# extraction genuinely produced no model. It must raise TransientExtraction on a retryable blip.
RecordFn = Callable[[str, str], Optional[ClausePropertyRecord]]
EscalateFn = Callable[[ClausePropertyRecord, str], bool]


class TransientExtraction(Exception):
    """A retryable extraction blip. Custom (not in LangGraph's default no-retry list) -> DEFAULT_RETRY retries."""


class ClauseExtractionState(TypedDict, total=False):
    clause_text: str
    record: Optional[ClausePropertyRecord]
    escalated: bool
    dead_letter: Optional[dict]


def _cheap_node(record_fn: RecordFn, model_id: str):
    def extract_cheap(state: ClauseExtractionState) -> ClauseExtractionState:
        with raw_llm_span("typed_clause_extraction.extract", model=model_id):
            record = record_fn(state["clause_text"], model_id)  # raises Transient -> retried; None -> dead-letter
        if record is None:
            return {"dead_letter": dead_letter("extraction_produced_no_models", model=model_id)}
        return {"record": reground(record, state["clause_text"])}  # ADR-0028 grounding gate

    return extract_cheap


def _strong_node(record_fn: RecordFn, model_id: str):
    def extract_strong(state: ClauseExtractionState) -> ClauseExtractionState:
        # Best-effort escalation: any failure keeps the cheap record we already have (never dead-letters here).
        try:
            with raw_llm_span("typed_clause_extraction.escalate", model=model_id):
                record = record_fn(state["clause_text"], model_id)
        except Exception:  # noqa: BLE001 - strong is best-effort; fall back to the cheap record
            return {"escalated": True}
        if record is None:
            return {"escalated": True}
        return {"record": reground(record, state["clause_text"]), "escalated": True}

    return extract_strong


def build_typed_clause_extraction(
    record_fn: RecordFn,
    *,
    cheap_model: str,
    strong_model: str,
    escalate_fn: EscalateFn = needs_escalation,
    enable_human_gate: bool = False,
    checkpointer: Any = None,
):
    """Compile the `typed_clause_extraction` subgraph. `record_fn` / `escalate_fn` are injected for testing;
    `checkpointer` is required only when `enable_human_gate` is True (interrupt() needs persistence)."""

    def route_after_cheap(state: ClauseExtractionState) -> str:
        if state.get("dead_letter"):
            return "end"
        if not state.get("escalated") and escalate_fn(state["record"], state["clause_text"]):
            return "strong"
        return "gate" if enable_human_gate else "end"

    def human_gate(state: ClauseExtractionState) -> ClauseExtractionState:
        record = state.get("record")
        if record is not None and escalate_fn(record, state["clause_text"]):
            interrupt({"reason": "low_confidence_extraction"})  # pause for review (needs a checkpointer)
        return {}

    g = StateGraph(ClauseExtractionState)
    g.add_node("extract_cheap", _cheap_node(record_fn, cheap_model), retry_policy=DEFAULT_RETRY)
    g.add_node("extract_strong", _strong_node(record_fn, strong_model))
    g.add_edge(START, "extract_cheap")

    targets = {"strong": "extract_strong", "end": END}
    if enable_human_gate:
        g.add_node("human_gate", human_gate)
        targets["gate"] = "human_gate"
        g.add_edge("human_gate", END)
        g.add_edge("extract_strong", "human_gate")
    else:
        g.add_edge("extract_strong", END)
    g.add_conditional_edges("extract_cheap", route_after_cheap, targets)
    return g.compile(checkpointer=checkpointer)


def production_record_fn() -> RecordFn:
    """Wire the real docling-graph extractor into `(text, model_id) -> ClausePropertyRecord | None`, mapping a
    transient failure to `TransientExtraction` (retried) and a genuine no-model result to `None` (dead-letter).

    NOTE: the caller must set `chunk_id`/`function`/`span_id` for `clause_to_record`; production wiring passes a
    partial. Grounding (`reground`) is applied by the subgraph node, not here.
    """
    from rag_wright.capabilities.dg_extraction import extract_clause, openrouter_model
    from rag_wright.contracts.identifiers import ChunkId
    from rag_wright.spans.clause_kg_extractor import clause_to_record

    def extract(text: str, model_id: str) -> Optional[ClausePropertyRecord]:
        model = openrouter_model("clause-extract", model_id)
        try:
            clause = extract_clause(text, model)
        except Exception as exc:  # noqa: BLE001 - provider/pipeline errors are transient -> retried
            raise TransientExtraction(str(exc)) from exc
        if clause is None:
            return None
        return clause_to_record(clause, chunk_id=ChunkId.of("clause", 0, text), function="", span_id="")

    return extract


def register_typed_clause_extraction(registry) -> None:
    """LG-1: register `typed_clause_extraction` (subgraph; the hardened LangGraph extract->adapt->reground)."""
    registry.register(
        "typed_clause_extraction",
        contract=ClausePropertyRecord,
        kind="subgraph",
        display_name="Typed clause extraction",
    )
