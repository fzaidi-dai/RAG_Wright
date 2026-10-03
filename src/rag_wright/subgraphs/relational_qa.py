"""LG-3a: `relational_qa` as a composite LangGraph subgraph -- the REFERENCE composite.

Answers an entity question with a grounded, cited answer built from the GRAPH STRUCTURE itself (the standing
rule: the graph is the relationship layer). No chunk-text rehydration: a relational fact IS the traversed edge.

    START --> traverse [RetryPolicy]  (graph_query: entity -> cited relationship evidence, FR-C.5)
                 |
                 v
             assemble  (graph_structural_evidence: each reached entity -> a cited relationship statement)
                 |
                 v
             generate  (generate_answer: grounded/cited/abstaining answer, FR-Q.6)  --> END

Why graph-structural, not chunk text: the CONTRACTS_WITH edges are co-party facts extracted from the contract
preamble; their provenance `chunk_id` is a whole-document id whose text is not stored for retrieval. Rehydrating
it would need a throwaway text store, and citing "a" same-relationship span would be a one-to-many guess. Instead
the evidence is the relationship + the target entity, cited by the SOURCE CONTRACT (parsed from the edge's
provenance chunk_id) -- verifiable and faithful, with nothing to rehydrate (A2, MCP-PROTO Phase A).

Query-side posture (matches `query_constraint_extraction`, LG-2a): hardening applied JUDICIOUSLY.
  - **traverse** is the only retried node (a graph/store IO blip is transient). On retry exhaustion it DEGRADES
    to empty evidence rather than dead-lettering -- the generator then abstains, so the query survives.
  - **assemble** is a pure, deterministic graph->evidence step (no store read -> no orphan, no dead_letter).
  - **generate** enforces no-claim-without-a-citation in code (FR-Q.6): empty evidence abstains with no model
    call; fabricated citations are dropped. Confidence tags surfaced by graph_query (FR-S.4) thread through.

`traverse_fn` / `generate_fn` are dependency-injected so the graph is hermetically testable with stubs.
`production_relational_qa` wires the real `graph_query` + `generate_answer`.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from rag_wright.capabilities.answer_generator import EvidenceItem, GeneratedAnswer
from rag_wright.capabilities.graph_query import GraphAnswer
from rag_wright.contracts.ontology import RelationshipType
from rag_wright.subgraphs.scaffold import DEFAULT_RETRY, business_span
from rag_wright.subgraphs.typed_clause_extraction import TransientExtraction  # shared retryable-blip signal

# traverse_fn: (start_entity_id, relationship_type, max_hops) -> GraphAnswer (must raise on a transient blip).
TraverseFn = Callable[[str, RelationshipType, int], GraphAnswer]
# ASYNC-C1 (ADR-0057): generate_fn is async (the model call gets a true wall-clock deadline via the seam).
GenerateFn = Callable[[str, list[EvidenceItem]], Awaitable[GeneratedAnswer]]

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


def graph_structural_evidence(graph_answer: GraphAnswer) -> list[EvidenceItem]:
    """The relational evidence IS the graph structure. Each reached entity yields one cited fact per source
    contract: "<start> <rel> <target> (per <contract>)", cited by the CONTRACT id parsed from the edge's
    provenance chunk_id (`<source_doc_id>:<index>:<hash>` -> source_doc_id = contract_id) -- verifiable, with no
    chunk text to rehydrate. Confidence = the path's first surfaced edge tag. Falls back to citing the target
    entity_id when an edge carries no chunk provenance. First-wins/dedup on (target, citation)."""
    start = graph_answer.start_entity_id
    rel = graph_answer.relationship_type
    items: list[EvidenceItem] = []
    seen: set[tuple[str, str]] = set()
    for ev in graph_answer.evidence:
        conf = ev.confidences[0] if ev.confidences else None
        contracts = sorted({cid.split(":")[0] for cid in ev.chunk_ids if cid}) or [ev.entity_id]
        for cite in contracts:
            key = (ev.entity_id, cite)
            if key in seen:
                continue
            seen.add(key)
            items.append(EvidenceItem(
                chunk_id=cite,
                text=f"{start} {rel} {ev.name} (entity {ev.entity_id}; per {cite})",
                confidence=conf))
    return items


def build_relational_qa(traverse_fn: TraverseFn, generate_fn: GenerateFn, *, retry_policy: Any = DEFAULT_RETRY):
    """Compile the `relational_qa` subgraph. `traverse_fn` / `generate_fn` are injected for hermetic testing;
    the graph->evidence assembly is a pure deterministic node. `retry_policy` is the traverse node's policy."""
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
                    return {"graph_answer": GraphAnswer(
                        start_entity_id=start, relationship_type=rel.value, evidence=[])}
                raise TransientExtraction(str(exc)) from exc
        return {"graph_answer": graph_answer}

    def assemble(state: RelationalQAState) -> RelationalQAState:
        with business_span("relational_qa.assemble"):
            return {"evidence": graph_structural_evidence(state["graph_answer"])}

    async def generate(state: RelationalQAState) -> RelationalQAState:
        with business_span("relational_qa.generate"):
            return {"answer": await generate_fn(state["query"], state.get("evidence", []))}

    g = StateGraph(RelationalQAState)
    g.add_node("traverse", traverse, retry_policy=retry_policy)
    g.add_node("assemble", assemble)
    g.add_node("generate", generate)
    g.add_edge(START, "traverse")
    g.add_edge("traverse", "assemble")
    g.add_edge("assemble", "generate")
    g.add_edge("generate", END)
    return g.compile()


def production_relational_qa(*, store: Any, answer_model: Any):
    """Wire the real `graph_query` + `generate_answer` into the composite (no text_store: the evidence is
    graph-structural). Imports are lazy so the module stays import-light and hermetic (tests inject stubs)."""
    from rag_wright.capabilities.answer_generator import agenerate_answer
    from rag_wright.capabilities.graph_query import graph_query

    def traverse(start: str, rel: RelationshipType, max_hops: int) -> GraphAnswer:
        # graph_query is domain-free and takes a generic edge-type string; this contract-reference leg passes the
        # enum's value (the contract vocab stays on the caller side, not in the generic primitive).
        return graph_query(start, store=store, relationship_type=rel.value, max_hops=max_hops)

    async def generate(query: str, evidence: list[EvidenceItem]) -> GeneratedAnswer:
        return await agenerate_answer(query, evidence, model=answer_model)

    return build_relational_qa(traverse, generate)


def register_relational_qa(registry) -> None:
    """LG-3a: register `relational_qa` (composite subgraph; graph_query -> graph-structural evidence ->
    generate_answer)."""
    registry.register(
        "relational_qa",
        contract=GeneratedAnswer,
        kind="subgraph",
        display_name="Relational QA (cited answer from graph traversal)",
    )


async def ainvoke(resources, inputs: dict):
    """EP-CORE-2 (ADR-0118): the capability invoke factory (impl_ref target)."""
    from rag_wright.capabilities.answer_generator import answer_model_for
    from rag_wright.models.profiles import ModelRole

    graph = production_relational_qa(store=resources._store,
                                     answer_model=answer_model_for(resources.model_id(ModelRole.GENERAL)))
    return await graph.ainvoke({"query": inputs["query"], "start_entity_id": inputs["start_entity_id"],
                                "max_hops": inputs.get("max_hops", 1)})
