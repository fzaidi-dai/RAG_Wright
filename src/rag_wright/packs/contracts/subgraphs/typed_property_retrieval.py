"""LEGB-SUBGRAPH (FR-Q, ADR-0033): `typed_property_retrieval` -- the property-boosted Leg B as a hardened
LangGraph subgraph, so the query WORKFLOW is a registered ARD `kind="subgraph"` (the LG-3 declared pattern),
not an imperative script.

A thin composite that wires the registered front-door capability + the registered retrieval capability.
Flow: `extract_constraints` (LLM: query -> typed (dim,value) constraints) -> `retrieve` (runs
`property_boosted_retrieval` over the WHOLE-INDEX BGE pool -> edge.span_id join -> typed_constraint_match_rank ->
cited spans) -> `judge_relevance` (issue 0023: a per-span relevance VERDICT against the condition, so `not_found`
is reachable) -> `assemble`. START -> extract_constraints -> retrieve -> judge_relevance -> assemble -> END.

ADR-0047: the precomputed clause-function pre-filter was RETIRED (graded-recall showed ON~=OFF, ceiling 0.969,
and the gate is how a mislabel corrupts retrieval). The `classify_functions` node is gone; retrieval is over the
whole index, ranked by property boost + BGE, so a mislabeled clause simply doesn't rank rather than polluting a
filtered pool. Each IO node degrades to EMPTY on exhausted retries (a query survives, never crashes). The two
seams are injected for hermetic testing; `production_typed_property_retrieval` wires the real granite front-door +
`property_boosted_retrieval` over the store + A100/local encoders. `property_boosted_retrieval` stays the
registered FUNCTION capability; this subgraph composes it.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from pydantic import BaseModel

from rag_wright.capabilities.document_scope import UnknownDocumentError
from rag_wright.packs.contracts.capabilities.property_boosted_retrieval import RankedSpan
from rag_wright.capabilities.span_relevance_judgment import Condition, RelevanceVerdict, finalize_verdict
from rag_wright.subgraphs.scaffold import DEFAULT_RETRY, business_span
from rag_wright.packs.contracts.subgraphs.typed_clause_extraction import TransientExtraction  # shared retryable-blip signal

# issue 0034: distinguishes "documents absent from the invoke state" (-> use the build-time default) from an
# explicit invoke-time value (a list, [] for scope-to-nothing, or None for the whole corpus).
_UNSET_DOCUMENTS = object()

# ASYNC-C1 (ADR-0057): constraints_fn is async (its model call gets a true wall-clock deadline via aextract_clause).
ConstraintsFn = Callable[[str], Awaitable[set]]  # query -> typed (dimension, value) constraints
# (query, constraints, documents_override) -> [RankedSpan]. `documents_override` is the invoke-time scope or
# `_UNSET_DOCUMENTS` (issue 0034: use the build-time default). ADR-0047: whole-index pool, no function filter.
RetrieveFn = Callable[[str, set, Any], list]
# issue 0023: (spans, condition) -> a RAW RelevanceVerdict per span, order-preserved (1:1). Injected + optional.
RelevanceJudgeFn = Callable[[list[RankedSpan], Condition], Awaitable[list[RelevanceVerdict]]]


class JudgedSpan(BaseModel):
    """A retrieved span composed with its relevance verdict (issue 0023) -- the subgraph's composition of a
    retrieval fact (`span`) and a judgement fact (`relevance`), mirroring how the compliance subgraph composes a
    claim + requirement + verdict into a `ComplianceFinding` rather than growing a field on the input contract.
    `relevance` is None ONLY when no judge was wired (the whole stage off) -- a distinct state from the `uncertain`
    VERDICT (the judge ran and could not decide). Within a judged sweep every span carries a real verdict."""

    span: RankedSpan
    relevance: RelevanceVerdict | None = None


class TypedPropertyRetrieval(BaseModel):
    """The `typed_property_retrieval` output: the query and its property-boosted, cited, RELEVANCE-JUDGED spans
    (FR-Q.6, issue 0023). Each result carries the retrieved span and its per-span relevance verdict, so the caller
    can group matched / possible / not_found without choosing a similarity threshold."""

    query: str
    results: list[JudgedSpan]


class _State(TypedDict, total=False):
    query: str
    clause_type: str          # issue 0023: the condition's clause type (the primary relevance test)
    value_condition: str      # issue 0023: the condition's narrower test (often absent/shared in a sweep)
    documents: Optional[list]  # issue 0034: per-INVOKE workspace scope; absent -> build-time default, [] -> nothing
    constraints: set
    results: list             # [RankedSpan] from retrieve
    judged: list              # [JudgedSpan] from judge_relevance
    retrieval: TypedPropertyRetrieval


def _degrading_io(name: str, work: Callable[[], dict], empty: dict, runtime: Runtime, max_attempts: int) -> dict:
    """Run an IO node's `work`: a transient blip re-raises so the RetryPolicy retries, EXCEPT on the final
    attempt where it degrades to `empty` (the query survives -- never a crash)."""
    attempt = runtime.execution_info.node_attempt
    with business_span(name):
        try:
            return work()
        except UnknownDocumentError:
            raise  # issue 0034: a bad workspace selection is a HARD error -- never retried or degraded to empty
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
    relevance_judge: RelevanceJudgeFn | None = None,
    retry_policy: Any = DEFAULT_RETRY,
):
    """Compile the `typed_property_retrieval` subgraph. The IO seams are injected for hermetic testing;
    `retry_policy` is each IO node's policy (overridable for fast tests). ADR-0047: no function pre-filter --
    `retrieve` runs over the whole-index pool with the property boost. `relevance_judge` (issue 0023) is optional:
    when wired (and a `clause_type` is in the input) every retrieved span is judged relevant/not_relevant/uncertain
    against the condition, so `not_found` is reachable; when None, spans pass through unjudged (`relevance=None`)."""
    max_attempts = int(getattr(retry_policy, "max_attempts", 3))

    async def extract_constraints(state: _State, runtime: Runtime) -> _State:
        async def _work() -> dict:
            return {"constraints": set(await constraints_fn(state["query"]))}

        return await _adegrading_io("typed_property_retrieval.extract_constraints", _work,
                                    {"constraints": set()}, runtime, max_attempts)

    def retrieve(state: _State, runtime: Runtime) -> _State:
        # issue 0034: a per-INVOKE `documents` scope wins over the build-time default; ABSENT -> the default.
        documents = state["documents"] if "documents" in state else _UNSET_DOCUMENTS
        return _degrading_io(
            "typed_property_retrieval.retrieve",
            lambda: {"results": retrieve_fn(state["query"], state.get("constraints", set()), documents)},
            {"results": []}, runtime, max_attempts)

    async def judge_relevance(state: _State, runtime: Runtime) -> _State:
        results: list[RankedSpan] = state.get("results", [])
        clause_type = state.get("clause_type")
        if relevance_judge is None or not clause_type or not results:
            # no judge wired (or no condition / nothing retrieved) -> pass through UNJUDGED (relevance None)
            return {"judged": [JudgedSpan(span=r) for r in results]}
        condition = Condition(clause_type=clause_type, value_condition=state.get("value_condition"),
                              question=state.get("query"))

        async def _work() -> dict:
            raw = await relevance_judge(results, condition)
            return {"judged": [JudgedSpan(span=r, relevance=finalize_verdict(v)) for r, v in zip(results, raw)]}

        # degrade: a wired judge that fails all attempts still gives every span a verdict (conservative uncertain),
        # never None -- so "judged-but-uncertain" is never confused with "not judged".
        empty = {"judged": [JudgedSpan(span=r, relevance=finalize_verdict(None)) for r in results]}
        return await _adegrading_io("typed_property_retrieval.judge_relevance", _work, empty, runtime, max_attempts)

    def assemble(state: _State) -> _State:
        return {"retrieval": TypedPropertyRetrieval(query=state["query"], results=state.get("judged", []))}

    g = StateGraph(_State)
    g.add_node("extract_constraints", extract_constraints, retry_policy=retry_policy)
    g.add_node("retrieve", retrieve, retry_policy=retry_policy)
    g.add_node("judge_relevance", judge_relevance, retry_policy=retry_policy)
    g.add_node("assemble", assemble)
    g.add_edge(START, "extract_constraints")
    g.add_edge("extract_constraints", "retrieve")
    g.add_edge("retrieve", "judge_relevance")
    g.add_edge("judge_relevance", "assemble")
    g.add_edge("assemble", END)
    return g.compile()


# issue 0020: query-side structured output is CLIENT-SIDE tag-parse (ADR-0045, the query-side standing rule), NOT
# the document-shaped docling-graph Clause template. That template sends ~6k tokens of INGESTION schema (40% of it
# phrase-cue descriptions a query does not need) to parse an ~88-char question, for ~26 tokens of output, and makes
# TWO calls (the gleaning pass, issue 0019). Tag-parse over the SAME Clause contract is ~62% fewer tokens and ONE
# call, and is the engine's own query-side path. A-lean (a description-free query variant) is the planned follow-up.
_QUERY_CONSTRAINT_PROMPT = (
    "Identify the typed clause properties this QUERY is asking about, and their values. Fill ONLY the tags whose "
    "property the query actually mentions; omit every other tag.\n\nQUERY: {query}")


async def aquery_constraints(query: str, model_id: str, *, structured_factory=None) -> set[tuple[str, str]]:
    """Extract a query's typed `(dimension, value)` constraints via client-side tag-parse over the `Clause`
    contract (issue 0020, ADR-0045). Same `Clause -> clause_to_record` mapping as the ingestion path, so the
    constraints are identical in shape; a QUERY carries no clause function (the `NO_FUNCTION` sentinel -- only the
    extracted properties are used). A persistent parse failure degrades to NO constraints (never a hard error), so
    retrieval proceeds over the whole-index pool with no boost. `structured_factory` is injected for hermetic tests
    (defaults to `build_tag_structured`)."""
    from rag_wright.packs.contracts.schemas.function import NO_FUNCTION
    from rag_wright.contracts.identifiers import ChunkId
    from rag_wright.packs.contracts.ontology.clause_template import Clause
    from rag_wright.packs.contracts.spans.clause_kg_extractor import clause_to_record

    if structured_factory is None:
        from rag_wright.models.tag_structured import build_tag_structured
        structured_factory = build_tag_structured
    try:
        clause = await structured_factory(model_id, Clause, label="query-constraints").ainvoke(
            _QUERY_CONSTRAINT_PROMPT.format(query=query))
    except Exception:  # noqa: BLE001 - a persistent client-side parse failure -> no constraints, not a crash
        return set()
    if clause is None:
        return set()
    rec = clause_to_record(clause, chunk_id=ChunkId.of("q", 0, query), function=NO_FUNCTION)
    return {(a.dimension.value, a.value) for a in rec.assertions}


def production_typed_property_retrieval(
    *, store: Any, embedder: Any, extract_model: Any, k: int = 8, pool_k: int = 30,
    judge_model_id: Any = None, documents: Optional[list[str]] = None,
):
    """Wire the real Leg B: query constraint-extraction (tag-parse, issue 0020) + the `property_boosted_retrieval`
    capability over the store + encoders (local or the A100 adapters). ADR-0047: no function classifier -- the pool
    is whole-index. `extract_model` may be an `ExtractionModel` (its `.model` slug is used) or a plain model id.

    `judge_model_id` (issue 0023): when given, wire the per-span relevance judge -- every returned span is judged
    relevant/not_relevant/uncertain against the condition (the graph input's `clause_type` + `value_condition`), so
    the product can reach `not_found` without a threshold. Omit it and spans pass through unjudged (`relevance`
    None), unchanged behaviour. The judge runs the returned `k` spans CONCURRENTLY; `k` is the caller's cost lever.

    `documents` (issue 0031 + 0034): scope the sweep to a workspace's source documents, applied IN THE STORE so
    out-of-scope spans are never pooled, reranked, or judged. This is the BUILD-TIME DEFAULT; because the
    compiled graph is a per-customer, process-lifetime object, a per-request workspace scope is passed at INVOKE
    time instead -- `graph.ainvoke({"query": ..., "documents": [...]})` -- and the invoke-time value WINS over
    this default (absent from the state -> this default; issue 0034). `None` = the whole corpus; an unknown id
    RAISES `UnknownDocumentError` (validated at invoke time, past the degrade so it is never silently emptied);
    `[]` = scope-to-nothing (no results)."""
    from rag_wright.capabilities.document_scope import validate_documents
    from rag_wright.packs.contracts.capabilities.property_boosted_retrieval import property_boosted_retrieval
    from rag_wright.packs.contracts.schemas.value_match import constraint_match_count  # the contract-domain (dim,value) matcher

    model_id = getattr(extract_model, "model", extract_model)  # ExtractionModel.model, or a bare id

    async def constraints_fn(query: str) -> set:
        return await aquery_constraints(query, model_id)

    def retrieve_fn(query: str, constraints: set, documents_override: Any = _UNSET_DOCUMENTS) -> list:
        # issue 0034: the per-INVOKE scope wins; ABSENT (the sentinel) -> the build-time `documents` default.
        docs = documents if documents_override is _UNSET_DOCUMENTS else documents_override
        validate_documents(store, docs)  # invoke-time: reject an unknown document BEFORE any retrieval spends
        # ADR-0047: functions=() -> property_boosted_retrieval runs over the WHOLE-INDEX BGE pool (no gate).
        return property_boosted_retrieval(
            query, store=store, embedder=embedder, functions=(), constraints=constraints, k=k, pool_k=pool_k,
            documents=docs, match_count_fn=constraint_match_count)  # EP-CORE-1b: inject the domain matcher

    relevance_judge: RelevanceJudgeFn | None = None
    if judge_model_id is not None:
        from rag_wright.capabilities.span_relevance_judgment import ajudge_spans, build_arelevance_judge_fn
        jid = getattr(judge_model_id, "model", judge_model_id)  # accept an ExtractionModel or a bare id
        _ajudge = build_arelevance_judge_fn(jid)

        async def relevance_judge(spans: list[RankedSpan], condition: Condition) -> list[RelevanceVerdict]:
            # judge exactly the returned spans, concurrently; matched[] passed as CONTEXT (evidence, not verdict)
            return await ajudge_spans([(s.text, s.matched) for s in spans], condition, ajudge_fn=_ajudge)

    return build_typed_property_retrieval(constraints_fn, retrieve_fn, relevance_judge=relevance_judge)


def register_typed_property_retrieval(registry) -> None:
    """LEGB-SUBGRAPH: register `typed_property_retrieval` (composite subgraph; front-door + property-boost rerank)."""
    registry.register(
        "typed_property_retrieval",
        contract=TypedPropertyRetrieval,
        kind="subgraph",
        display_name="Typed property-boosted retrieval (Leg B)",
    )


async def ainvoke(resources, inputs: dict):
    """EP-CORE-2 (ADR-0118): the capability invoke factory (impl_ref target) -- build Leg B over the opaque
    workspace handle and run it. `inputs`: query (+ optional k/pool_k/documents)."""
    from rag_wright.packs.contracts.capabilities.dg_extraction import default_extraction_model
    from rag_wright.models.profiles import ModelRole

    graph = production_typed_property_retrieval(
        store=resources._store, embedder=resources._embedder,
        extract_model=default_extraction_model(model=resources.model_id(ModelRole.STRUCTURED_REASONING)),
        k=inputs.get("k", 8), pool_k=inputs.get("pool_k", 30), documents=inputs.get("documents"))
    return await graph.ainvoke({"query": inputs["query"]})
