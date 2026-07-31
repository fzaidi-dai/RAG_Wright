"""LG-3d: `contract_ingestion_pipeline` -- the GENERIC ingestion pipeline as a composite LangGraph subgraph.

Source documents -> a populated, connected contract KG, composing the LG-1/LG-2 ingest subgraphs + the
ingest-side capabilities. The pipeline is corpus-AGNOSTIC; each per-document ingest runs:

        START --> chunk [RetryPolicy]        (semantic_chunking: text -> chunks)
                    |
              +-----+-----+                  (the two extraction paths fan out in parallel)
              v           v
        extract_clauses  extract_graph       (segment + typed_clause_extraction || graph_extraction)
              +-----+-----+
                    v
                 resolve                      (entity_resolution: mentions -> canonical entities)
                    v
                  write                       (typed_kg_write + write_graph)  --> END
                    |  (any stage fails)
                    +--> dead_letter --> END  (one bad document never kills the corpus ingest)

**The corpus seam (the whole point).** A `CorpusAdapter` yields `SourceDocument`s -- the ONLY per-corpus code.
Adding a corpus = writing one adapter (`documents() -> SourceDocument{canonical id, text, optional metadata}`),
NEVER re-implementing the flow: `run_corpus_ingestion(XYZAdapter(), pipeline)`, not an `ingest_xyz()`. Parsing
is the adapter's job (PDF via docling, CUAD from JSON, ...), so the generic pipeline starts from text.
`run_corpus_ingestion` maps every document through the pipeline (collecting per-document results and
dead-letters) then runs `party_clause_linking` ONCE at the end (KG-7) to connect the parties to the clauses.

Every stage is dependency-injected so the graph is hermetically testable with stubs -- no live LLM/store.
`production_contract_ingestion_pipeline` wires the real capabilities. The `source_doc_id` on every
`SourceDocument` MUST come from `canonical_source_doc_id` (HYG-1), so every graph shares one id scheme and the
KG-7 link is a clean join.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Optional, Protocol, TypedDict, runtime_checkable

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from pydantic import BaseModel

from rag_wright.subgraphs.scaffold import DEFAULT_RETRY, business_span, dead_letter
from rag_wright.subgraphs.typed_clause_extraction import TransientExtraction


class SourceDocument(BaseModel):
    """One document to ingest: its canonical `source_doc_id` (HYG-1), its already-parsed text, and optional
    per-corpus metadata (e.g. CUAD annotated parties, ACORD pre-segmented spans) the stages may consult."""

    source_doc_id: str
    text: str
    metadata: dict = {}


class IngestionReport(BaseModel):
    """The composite's output: how many documents were ingested, which dead-lettered (with reasons), and how
    many `PARTY_TO` links the final connect step wrote."""

    documents_ingested: int
    dead_lettered: list[dict]
    party_links: int
    per_document: list[dict]


@runtime_checkable
class CorpusAdapter(Protocol):
    """The ONE per-corpus seam: yield the corpus's documents as `SourceDocument`s (parsing + canonical id +
    any corpus metadata live here). Everything downstream is corpus-agnostic."""

    def documents(self) -> Iterable[SourceDocument]: ...


# The injected per-document stage seams (each wraps a built subgraph / capability; stubbed in tests).
ChunkFn = Callable[[SourceDocument], list]  # doc -> chunks
ClausesFn = Callable[[SourceDocument, list], list]  # (doc, chunks) -> typed clause records
GraphFn = Callable[[SourceDocument, list], list]  # (doc, chunks) -> ExtractionResults
ResolveFn = Callable[[list], Any]  # extraction results -> resolution
WriteFn = Callable[[SourceDocument, list, Any], dict]  # (doc, clause records, resolution) -> counts
LinkFn = Callable[[], int]  # corpus-level: party_clause_linking -> #PARTY_TO edges


class IngestionState(TypedDict, total=False):
    document: SourceDocument
    chunks: list
    clause_records: list
    extraction_results: list
    resolution: Any
    written: dict
    dead_letter: Optional[dict]


def build_document_ingest(
    chunk_fn: ChunkFn,
    clauses_fn: ClausesFn,
    graph_fn: GraphFn,
    resolve_fn: ResolveFn,
    write_fn: WriteFn,
    *,
    retry_policy: Any = DEFAULT_RETRY,
):
    """Compile the per-document ingest subgraph. All five stages are injected for hermetic testing;
    `retry_policy` is the chunk/extract nodes' policy. Any stage failure dead-letters the document (dropped
    with a reason, never raised) so one bad document never kills the corpus ingest."""
    max_attempts = int(getattr(retry_policy, "max_attempts", 3))

    def _guard(name: str, work: Callable[[], dict], runtime: Runtime, doc: SourceDocument) -> dict:
        attempt = runtime.execution_info.node_attempt
        with business_span(f"contract_ingestion.{name}", source_doc_id=doc.source_doc_id):
            try:
                return work()
            except Exception as exc:  # noqa: BLE001 - transient -> retry, or dead-letter on exhaustion
                if attempt >= max_attempts:
                    return {"dead_letter": dead_letter(
                        "ingest_failed", source_doc_id=doc.source_doc_id, stage=name, error=str(exc))}
                raise TransientExtraction(str(exc)) from exc

    def chunk(state: IngestionState, runtime: Runtime) -> IngestionState:
        doc = state["document"]
        return _guard("chunk", lambda: {"chunks": chunk_fn(doc)}, runtime, doc)

    def extract_clauses(state: IngestionState, runtime: Runtime) -> IngestionState:
        doc = state["document"]
        return _guard("extract_clauses",
                      lambda: {"clause_records": clauses_fn(doc, state.get("chunks", []))}, runtime, doc)

    def extract_graph(state: IngestionState, runtime: Runtime) -> IngestionState:
        doc = state["document"]
        return _guard("extract_graph",
                      lambda: {"extraction_results": graph_fn(doc, state.get("chunks", []))}, runtime, doc)

    def resolve(state: IngestionState) -> IngestionState:
        if state.get("dead_letter"):
            return {}
        with business_span("contract_ingestion.resolve"):
            return {"resolution": resolve_fn(state.get("extraction_results", []))}

    def write(state: IngestionState) -> IngestionState:
        if state.get("dead_letter"):
            return {}
        doc = state["document"]
        with business_span("contract_ingestion.write", source_doc_id=doc.source_doc_id):
            return {"written": write_fn(doc, state.get("clause_records", []), state.get("resolution"))}

    def _route_after_chunk(state: IngestionState):
        # chunk failed -> dead-letter straight to END; else fan out to BOTH extraction paths (parallel)
        return "end" if state.get("dead_letter") else ["clauses", "graph"]

    g = StateGraph(IngestionState)
    g.add_node("chunk", chunk, retry_policy=retry_policy)
    g.add_node("extract_clauses", extract_clauses, retry_policy=retry_policy)
    g.add_node("extract_graph", extract_graph, retry_policy=retry_policy)
    g.add_node("resolve", resolve)
    g.add_node("write", write)

    g.add_edge(START, "chunk")
    g.add_conditional_edges(
        "chunk", _route_after_chunk,
        {"clauses": "extract_clauses", "graph": "extract_graph", "end": END})
    g.add_edge("extract_clauses", "resolve")  # resolve joins both extraction paths
    g.add_edge("extract_graph", "resolve")
    g.add_edge("resolve", "write")
    g.add_edge("write", END)
    return g.compile()


def run_corpus_ingestion(
    adapter: CorpusAdapter, ingest_graph: Any, *, link_fn: LinkFn = lambda: 0
) -> IngestionReport:
    """Map every document from `adapter` through the per-document `ingest_graph`, then run `link_fn`
    (party_clause_linking, KG-7) ONCE to connect parties to clauses. A dead-lettered document is recorded and
    skipped -- the corpus ingest survives one bad document. The link runs after all writes so the PARTY_TO edges
    see every contract."""
    ingested = 0
    dead_lettered: list[dict] = []
    per_document: list[dict] = []
    for document in adapter.documents():
        out = ingest_graph.invoke({"document": document})
        if out.get("dead_letter"):
            dead_lettered.append(out["dead_letter"])
            continue
        ingested += 1
        per_document.append({"source_doc_id": document.source_doc_id, "written": out.get("written", {})})

    party_links = link_fn()
    return IngestionReport(
        documents_ingested=ingested, dead_lettered=dead_lettered,
        party_links=party_links, per_document=per_document)


class CuadAdapter:
    """The REFERENCE `CorpusAdapter` (ADR-locked design): CUAD -> `SourceDocument`s. It is the ONLY CUAD-specific
    code in the ingest path -- it parses the corpus (CUAD ships text in JSON, so no docling parse), assigns the
    ONE canonical `source_doc_id` (HYG-1), and passes the raw title as metadata. Adding another corpus means
    writing a sibling adapter (e.g. `AcordAdapter` carrying pre-segmented spans in `metadata`); the pipeline and
    driver do not change. `run_corpus_ingestion(CuadAdapter(path), ingest_graph, link_fn=...)` ingests it."""

    def __init__(self, cuad_path: Any, *, limit: int = 0) -> None:
        self._path = cuad_path
        self._limit = limit

    def documents(self) -> Iterable[SourceDocument]:
        from rag_wright.contracts.identifiers import canonical_source_doc_id
        from rag_wright.spans.cuad_labels import parse_cuad

        contracts = list(parse_cuad(self._path))
        if self._limit:
            contracts = contracts[: self._limit]
        for contract in contracts:
            yield SourceDocument(
                source_doc_id=canonical_source_doc_id(contract.contract_id),
                text=contract.context,
                metadata={"raw_title": contract.contract_id},
            )


# Wiring the production stage seams (deployment glue -- store/models/adapter specific, so not a fixed function):
#   chunk_fn      -> semantic_chunking (build_semantic_chunking) over the doc text
#   clauses_fn    -> segment_clause per chunk + typed_clause_extraction (build_typed_clause_extraction) per span
#   graph_fn      -> graph_extraction (build_graph_extraction(default_extractors())) per chunk
#   resolve_fn    -> disambiguate + entity_resolution.resolve_entities over the ExtractionResults
#   write_fn      -> store.write_clause_kg (clause records) + store.write_graph (resolved entities)
#   link_fn       -> party_clause_linking(store)  (KG-7, run once by run_corpus_ingestion)
# Test on a FEW docs first (CuadAdapter(path, limit=N)); never a full re-ingest without intent (CUAD-FULL-COVERAGE).


def register_contract_ingestion_pipeline(registry) -> None:
    """LG-3d: register `contract_ingestion_pipeline` (composite subgraph; source docs -> populated contract KG)."""
    registry.register(
        "contract_ingestion_pipeline",
        contract=IngestionReport,
        kind="subgraph",
        display_name="Contract ingestion pipeline (corpus -> populated, connected KG)",
    )
