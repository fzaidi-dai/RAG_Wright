"""LG-3a: `relational_qa` as a composite LangGraph subgraph -- the REFERENCE composite.

Composes three already-built, registered capabilities into one hardened workflow that answers an entity
question with a grounded, cited answer:

    START --> traverse [RetryPolicy]  (graph_query: entity -> cited graph evidence, FR-C.5)
                 |
                 v
             rehydrate  (chunk_read: evidence chunk_ids -> full text, T38)  --orphan--> dead_letter --> END
                 |
                 v
             generate  (generate_answer: grounded/cited/abstaining answer, FR-Q.6)  --> END

Query-side posture (matches `query_constraint_extraction`, LG-2a): hardening applied JUDICIOUSLY.
  - **traverse** is the only retried node (a graph/store IO blip is transient). On retry exhaustion it
    DEGRADES to empty evidence rather than dead-lettering -- the generator then abstains, so the query
    survives (never a dropped item).
  - **rehydrate** turns the evidence's `chunk_id`s into text. `chunk_read` raises on an orphaned id (a real
    pipeline inconsistency, never a silent drop); the subgraph surfaces that as a **dead_letter** with the
    reason rather than fabricating an answer -- the batch survives and the inconsistency is visible.
  - **generate** enforces no-claim-without-a-citation in code (FR-Q.6): empty evidence abstains with no model
    call; fabricated citations are dropped. Confidence tags surfaced by graph_query (FR-S.4) thread through
    the evidence into the generator.

The three capability calls are dependency-injected (`traverse_fn` / `rehydrate_fn` / `generate_fn`) so the
graph is hermetically testable with stubs -- no live LLM or store. `production_relational_qa` wires the real
`graph_query` + `chunk_read` + `generate_answer`. None of the three is a raw-SDK call (graph_query/chunk_read
are pure functions; generate_answer goes through the LangChain seam, auto-captured), so per-node visibility is
a `business_span`, not `raw_llm_span`.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from rag_wright.capabilities.answer_generator import EvidenceItem, GeneratedAnswer
from rag_wright.capabilities.chunk_read import ChunkReadResult
from rag_wright.capabilities.graph_query import GraphAnswer
from rag_wright.contracts.ontology import RelationshipType
from rag_wright.subgraphs.scaffold import DEFAULT_RETRY, business_span, dead_letter
from rag_wright.subgraphs.typed_clause_extraction import TransientExtraction  # shared retryable-blip signal

# traverse_fn: (start_entity_id, relationship_type, max_hops) -> GraphAnswer (must raise on a transient blip).
TraverseFn = Callable[[str, RelationshipType, int], GraphAnswer]
RehydrateFn = Callable[[list[str]], ChunkReadResult]  # chunk_ids -> rehydrated text (raises on an orphan id)
GenerateFn = Callable[[str, list[EvidenceItem]], GeneratedAnswer]

DEFAULT_RELATIONSHIP = RelationshipType.CONTRACTS_WITH


class RelationalQAState(TypedDict, total=False):
    query: str
    start_entity_id: str
    relationship_type: RelationshipType
    max_hops: int
    graph_answer: GraphAnswer
    evidence: list[EvidenceItem]
    answer: GeneratedAnswer
    dead_letter: Optional[dict]


def _evidence_pairs(graph_answer: GraphAnswer) -> list[tuple[str, Optional[str]]]:
    """Flatten the graph evidence to (chunk_id, confidence) pairs, first-wins and order-preserving.

    Each `GraphEvidence` carries per-edge `chunk_ids` and parallel `confidences` (FR-C.5 surfaces, never
    filters); a chunk_id keeps the confidence of the first path edge that cited it.
    """
    seen: set[str] = set()
    pairs: list[tuple[str, Optional[str]]] = []
    for ev in graph_answer.evidence:
        for chunk_id, confidence in zip(ev.chunk_ids, ev.confidences):
            if chunk_id not in seen:
                seen.add(chunk_id)
                pairs.append((chunk_id, confidence))
    return pairs


def build_relational_qa(
    traverse_fn: TraverseFn,
    rehydrate_fn: RehydrateFn,
    generate_fn: GenerateFn,
    *,
    retry_policy: Any = DEFAULT_RETRY,
):
    """Compile the `relational_qa` subgraph. The three capability calls are injected for hermetic testing;
    `retry_policy` is the traverse node's policy (overridable for fast tests)."""
    max_attempts = int(getattr(retry_policy, "max_attempts", 3))

    def traverse(state: RelationalQAState, runtime: Runtime) -> RelationalQAState:
        # node_attempt is 1-indexed; a transient blip re-raises so the RetryPolicy retries, EXCEPT on the
        # final attempt where it degrades to EMPTY evidence (the generator abstains -- the query is never lost).
        attempt = runtime.execution_info.node_attempt
        start = state["start_entity_id"]
        rel = state.get("relationship_type", DEFAULT_RELATIONSHIP)
        with business_span("relational_qa.traverse", start_entity_id=start):
            try:
                graph_answer = traverse_fn(start, rel, state.get("max_hops", 1))
            except Exception as exc:  # noqa: BLE001 - transient -> retry, or degrade to empty on exhaustion
                if attempt >= max_attempts:
                    graph_answer = GraphAnswer(start_entity_id=start, relationship_type=rel.value, evidence=[])
                    return {"graph_answer": graph_answer}
                raise TransientExtraction(str(exc)) from exc
        return {"graph_answer": graph_answer}

    def rehydrate(state: RelationalQAState) -> RelationalQAState:
        pairs = _evidence_pairs(state["graph_answer"])
        if not pairs:
            return {"evidence": []}  # empty traversal -> empty evidence -> abstain (no store read)
        with business_span("relational_qa.rehydrate", chunk_count=len(pairs)):
            try:
                result = rehydrate_fn([chunk_id for chunk_id, _ in pairs])
            except KeyError as exc:  # an orphaned chunk_id is a pipeline inconsistency: surface, never fabricate
                return {"dead_letter": dead_letter(
                    "rehydration_orphan_chunk", start_entity_id=state["start_entity_id"], error=str(exc))}
        text_by_id = {chunk.chunk_id: chunk.text for chunk in result.chunks}
        evidence = [
            EvidenceItem(chunk_id=chunk_id, text=text_by_id[chunk_id], confidence=confidence)
            for chunk_id, confidence in pairs
        ]
        return {"evidence": evidence}

    def generate(state: RelationalQAState) -> RelationalQAState:
        with business_span("relational_qa.generate"):
            answer = generate_fn(state["query"], state.get("evidence", []))
        return {"answer": answer}

    g = StateGraph(RelationalQAState)
    g.add_node("traverse", traverse, retry_policy=retry_policy)
    g.add_node("rehydrate", rehydrate)
    g.add_node("generate", generate)
    g.add_edge(START, "traverse")
    g.add_edge("traverse", "rehydrate")
    g.add_conditional_edges("rehydrate", lambda s: "end" if s.get("dead_letter") else "generate",
                            {"generate": "generate", "end": END})
    g.add_edge("generate", END)
    return g.compile()


def production_relational_qa(*, store: Any, text_store: Any, answer_model: Any):
    """Wire the real `graph_query` + `chunk_read` + `generate_answer` into the composite. Imports are lazy so
    the subgraph module stays import-light and hermetic (tests inject stubs, never touch a store/LLM)."""
    from rag_wright.capabilities.answer_generator import generate_answer
    from rag_wright.capabilities.chunk_read import chunk_read
    from rag_wright.capabilities.graph_query import graph_query

    def traverse(start: str, rel: RelationshipType, max_hops: int) -> GraphAnswer:
        return graph_query(start, store=store, relationship_type=rel, max_hops=max_hops)

    def rehydrate(chunk_ids: list[str]) -> ChunkReadResult:
        return chunk_read(chunk_ids, text_store=text_store)

    def generate(query: str, evidence: list[EvidenceItem]) -> GeneratedAnswer:
        return generate_answer(query, evidence, model=answer_model)

    return build_relational_qa(traverse, rehydrate, generate)


def register_relational_qa(registry) -> None:
    """LG-3a: register `relational_qa` (composite subgraph; graph_query -> chunk_read -> generate_answer)."""
    registry.register(
        "relational_qa",
        contract=GeneratedAnswer,
        kind="subgraph",
        display_name="Relational QA (cited answer from graph traversal)",
    )
