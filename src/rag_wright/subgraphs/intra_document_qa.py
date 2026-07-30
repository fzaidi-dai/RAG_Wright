"""LG-3b: `intra_document_qa` as a composite LangGraph subgraph.

Answer a question scoped to ONE contract with a grounded, cited answer, composing the intra-contract scoped
KG query (KG-4, `contract_kg_serve`) with grounded answer generation (FR-Q.6):

    START --> serve [RetryPolicy]  (scoped KG query: contract_id + question -> cited CitedClauses)
                 |
                 v
             assemble [RetryPolicy]  (rehydrate each clause's REAL span text -> cited EvidenceItem)
                 |                     --orphan span--> dead_letter --> END
                 v
             generate  (generate_answer: grounded/cited/abstaining answer)  --> END

Query-side posture (matches `query_constraint_extraction` / `relational_qa`): judicious hardening.
  - **serve** retries a transient store blip; on retry exhaustion it DEGRADES to no clauses -> empty evidence
    -> the generator abstains, so the query is never dropped.
  - **assemble** rehydrates each served clause to its OPERATIVE SPAN TEXT (the clause node stores only
    id/function/folio; the text lives on the SPAN records, reached via each clause's `span_id` provenance).
    The evidence the generator reads is the real clause language, cited by `clause_id`, with the typed
    `(dimension, value)` facts appended and the worst-case property confidence surfaced (FR-S.4). A span_id
    the store cannot resolve (an orphan) dead-letters rather than fabricating; a property-less clause (no
    span) cites its function label. A transient rehydration blip retries, then dead-letters on exhaustion.
  - **generate** enforces no-claim-without-a-citation in code (FR-Q.6): empty evidence abstains with no model
    call; fabricated citations are dropped.

`serve_fn` / `clause_text_fn` / `generate_fn` are dependency-injected so the graph is hermetically testable
with stubs -- no live LLM or store. `production_intra_document_qa` wires the real scoped query
(function-classified + serve), the span-text rehydration (`spans_by_contract`), and `generate_answer`. None is
a raw-SDK call (serve/rehydrate are store reads; generate_answer uses the LangChain seam, auto-captured), so
per-node visibility is a `business_span`.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from rag_wright.capabilities.answer_generator import EvidenceItem, GeneratedAnswer
from rag_wright.capabilities.contract_kg_serve import CitedClause, CitedProperty
from rag_wright.subgraphs.scaffold import DEFAULT_RETRY, business_span, dead_letter
from rag_wright.subgraphs.typed_clause_extraction import TransientExtraction  # shared retryable-blip signal

# serve_fn: (contract_id, question) -> the scoped clauses (must raise on a transient store blip).
ServeFn = Callable[[str, str], list[CitedClause]]
# clause_text_fn: (contract_id, clauses) -> {clause_id: operative-span text}; a clause with a span_id that
# cannot be resolved raises KeyError (no silent drop); a property-less clause is simply absent from the map.
ClauseTextFn = Callable[[str, list[CitedClause]], dict[str, str]]
GenerateFn = Callable[[str, list[EvidenceItem]], GeneratedAnswer]

# Worst-case provenance surfaced to the generator: the least-trusted tag among a clause's properties wins.
_CONFIDENCE_ORDER = ("AMBIGUOUS", "INFERRED", "EXTRACTED")


class IntraDocumentQAState(TypedDict, total=False):
    contract_id: str
    question: str
    clauses: list[CitedClause]
    evidence: list[EvidenceItem]
    answer: GeneratedAnswer
    dead_letter: Optional[dict]


def _clause_confidence(properties: list[CitedProperty]) -> Optional[str]:
    tags = {p.confidence for p in properties if p.confidence}
    for tag in _CONFIDENCE_ORDER:  # worst-case first
        if tag in tags:
            return tag
    return None


def _clause_to_evidence(clause: CitedClause, body: Optional[str]) -> EvidenceItem:
    """One cited evidence item: the clause's REAL span text (when rehydrated) with its typed facts appended,
    cited by `clause_id`, confidence surfaced. A property-less clause with no span text cites its function."""
    facts = "; ".join(f"{p.dimension}={p.value}" for p in clause.properties)
    if body:
        text = f"{clause.function}: {body}" + (f" [{facts}]" if facts else "")
    elif facts:
        text = f"{clause.function} — {facts}"  # no span text (property-less path): the typed facts stand in
    else:
        text = clause.function
    return EvidenceItem(chunk_id=clause.clause_id, text=text, confidence=_clause_confidence(clause.properties))


def build_intra_document_qa(
    serve_fn: ServeFn,
    clause_text_fn: ClauseTextFn,
    generate_fn: GenerateFn,
    *,
    retry_policy: Any = DEFAULT_RETRY,
):
    """Compile the `intra_document_qa` subgraph. `serve_fn` / `clause_text_fn` / `generate_fn` are injected for
    hermetic testing; `retry_policy` is the serve and assemble nodes' policy (overridable for fast tests)."""
    max_attempts = int(getattr(retry_policy, "max_attempts", 3))

    def serve(state: IntraDocumentQAState, runtime: Runtime) -> IntraDocumentQAState:
        # node_attempt is 1-indexed; a transient blip re-raises so the RetryPolicy retries, EXCEPT on the
        # final attempt where it degrades to NO clauses (the generator abstains -- the query is never lost).
        attempt = runtime.execution_info.node_attempt
        with business_span("intra_document_qa.serve", contract_id=state["contract_id"]):
            try:
                clauses = serve_fn(state["contract_id"], state["question"])
            except Exception as exc:  # noqa: BLE001 - transient -> retry, or degrade to empty on exhaustion
                if attempt >= max_attempts:
                    return {"clauses": []}
                raise TransientExtraction(str(exc)) from exc
        return {"clauses": clauses}

    def assemble(state: IntraDocumentQAState, runtime: Runtime) -> IntraDocumentQAState:
        clauses = state.get("clauses", [])
        if not clauses:
            return {"evidence": []}
        attempt = runtime.execution_info.node_attempt
        contract_id = state["contract_id"]
        with business_span("intra_document_qa.assemble", clause_count=len(clauses)):
            try:
                texts = clause_text_fn(contract_id, clauses)
            except KeyError as exc:  # an orphan span_id is a pipeline inconsistency: surface, never fabricate
                return {"dead_letter": dead_letter(
                    "clause_text_orphan_span", contract_id=contract_id, error=str(exc))}
            except Exception as exc:  # noqa: BLE001 - transient store blip -> retry, or dead-letter on exhaust
                if attempt >= max_attempts:
                    return {"dead_letter": dead_letter(
                        "clause_text_rehydration_failed", contract_id=contract_id, error=str(exc))}
                raise TransientExtraction(str(exc)) from exc
        return {"evidence": [_clause_to_evidence(c, texts.get(c.clause_id)) for c in clauses]}

    def generate(state: IntraDocumentQAState) -> IntraDocumentQAState:
        with business_span("intra_document_qa.generate"):
            answer = generate_fn(state["question"], state.get("evidence", []))
        return {"answer": answer}

    g = StateGraph(IntraDocumentQAState)
    g.add_node("serve", serve, retry_policy=retry_policy)
    g.add_node("assemble", assemble, retry_policy=retry_policy)
    g.add_node("generate", generate)
    g.add_edge(START, "serve")
    g.add_edge("serve", "assemble")
    g.add_conditional_edges("assemble", lambda s: "end" if s.get("dead_letter") else "generate",
                            {"generate": "generate", "end": END})
    g.add_edge("generate", END)
    return g.compile()


def production_intra_document_qa(*, store: Any, answer_model: Any, function_model_id: str):
    """Wire the real scoped query + span-text rehydration + `generate_answer` into the composite. The scoped
    query classifies the question to its clause function(s) (`query_function_classification`) and serves those
    clauses (`clauses_of_function`), falling back to the whole per-contract index when no function is inferred.
    Rehydration maps each clause's `span_id` provenance to its operative-span text (`spans_by_contract`,
    contract-scoped and light -- no dense vectors).

    Imports are lazy so the subgraph module stays import-light and hermetic (tests inject stubs)."""
    from rag_wright.capabilities.answer_generator import generate_answer
    from rag_wright.capabilities.contract_kg_serve import clauses_of_function, contract_clause_index
    from rag_wright.capabilities.query_function_classifier import classify_query_functions

    def serve(contract_id: str, question: str) -> list[CitedClause]:
        functions = classify_query_functions(question, function_model_id)
        if not functions:  # no routable function -> serve the whole per-contract KG (the generator scopes)
            return contract_clause_index(store, contract_id)
        out: list[CitedClause] = []
        seen: set[str] = set()
        for function in functions:
            for clause in clauses_of_function(store, contract_id, function):
                if clause.clause_id not in seen:
                    seen.add(clause.clause_id)
                    out.append(clause)
        return out

    def clause_text(contract_id: str, clauses: list[CitedClause]) -> dict[str, str]:
        functions = sorted({c.function for c in clauses if c.function})
        text_by_span = {row["span_id"]: row["text"] for row in store.spans_by_contract(contract_id, functions)}
        out: dict[str, str] = {}
        for clause in clauses:
            span_ids = list(dict.fromkeys(p.span_id for p in clause.properties if p.span_id))
            if not span_ids:
                continue  # property-less clause: no operative span to quote; cite the function label
            bodies = []
            for span_id in span_ids:
                if span_id not in text_by_span:  # orphan: surfaced (KeyError), never a silent evidence drop
                    raise KeyError(
                        f"clause {clause.clause_id}: span {span_id!r} has no text in contract {contract_id}")
                bodies.append(text_by_span[span_id])
            out[clause.clause_id] = " ".join(bodies)
        return out

    def generate(question: str, evidence: list[EvidenceItem]) -> GeneratedAnswer:
        return generate_answer(question, evidence, model=answer_model)

    return build_intra_document_qa(serve, clause_text, generate)


def register_intra_document_qa(registry) -> None:
    """LG-3b: register `intra_document_qa` (composite subgraph; scoped KG query -> rehydrate -> generate)."""
    registry.register(
        "intra_document_qa",
        contract=GeneratedAnswer,
        kind="subgraph",
        display_name="Intra-document QA (cited answer scoped to one contract)",
    )
