"""LG-3d: `contract_ingestion_pipeline` -- the GENERIC ingestion pipeline as a composite LangGraph subgraph.

Source documents -> a populated, connected contract KG, composing the LG-1/LG-2 ingest subgraphs + the
ingest-side capabilities. The pipeline is corpus-AGNOSTIC; each per-document ingest runs:

        START --> chunk [RetryPolicy]        (semantic_chunking: text -> chunks)
                    v
                 segment                      (SHARED: segment + LegalBERT function-classify -> spans)
              /     |     \\                    (three branches fan out in parallel from the shared spans)
        extract   index    extract_graph      (clause extraction | dense/sparse Span index | graph extraction)
        _clauses  _spans
              \\     |     /
                 resolve                      (entity_resolution: mentions -> canonical entities)
                    v
                  write                       (write_clause_kg + write_graph; span index already written) --> END
                    |  (any stage fails)
                    +--> dead_letter --> END  (one bad document never kills the corpus ingest; the span
                                               index is best-effort -- its failure never dead-letters the doc)

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
SegmentFn = Callable[[SourceDocument, list], list]  # (doc, chunks) -> segments [(op, function, chunk_doc_start)]
ClausesFn = Callable[[SourceDocument, list], list]  # (doc, segments) -> typed clause records
IndexFn = Callable[[SourceDocument, list], int]  # (doc, segments) -> #Span records written (retrieval index)
GraphFn = Callable[[SourceDocument, list], list]  # (doc, chunks) -> ExtractionResults
ResolveFn = Callable[[list], Any]  # extraction results -> resolution
WriteFn = Callable[[SourceDocument, list, Any], dict]  # (doc, clause records, resolution) -> counts
LinkFn = Callable[[], int]  # corpus-level: party_clause_linking -> #PARTY_TO edges


class IngestionState(TypedDict, total=False):
    document: SourceDocument
    chunks: list
    segments: list  # shared segmentation: [(OperativeSpan, function, chunk_doc_start)] for clauses + span index
    clause_records: list
    span_count: int  # Span records written to the retrieval index (best-effort)
    extraction_results: list
    resolution: Any
    written: dict
    dead_letter: Optional[dict]


def build_document_ingest(
    chunk_fn: ChunkFn,
    segment_fn: SegmentFn,
    clauses_fn: ClausesFn,
    index_fn: IndexFn,
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

    def segment(state: IngestionState, runtime: Runtime) -> IngestionState:
        # Shared segmentation + function classification, consumed by BOTH clause extraction and the span index.
        doc = state["document"]
        return _guard("segment", lambda: {"segments": segment_fn(doc, state.get("chunks", []))}, runtime, doc)

    def extract_clauses(state: IngestionState, runtime: Runtime) -> IngestionState:
        doc = state["document"]
        return _guard("extract_clauses",
                      lambda: {"clause_records": clauses_fn(doc, state.get("segments", []))}, runtime, doc)

    def index_spans(state: IngestionState) -> IngestionState:
        # The dense/sparse retrieval index (Span records). BEST-EFFORT: a failed index must NOT lose the
        # document's clause KG / entity graph, so it degrades to 0 rather than dead-lettering.
        if state.get("dead_letter"):
            return {}
        doc = state["document"]
        with business_span("contract_ingestion.index_spans"):
            try:
                return {"span_count": index_fn(doc, state.get("segments", []))}
            except Exception:  # noqa: BLE001 - the retrieval index is a separate, best-effort output
                return {"span_count": 0}

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
            counts = write_fn(doc, state.get("clause_records", []), state.get("resolution"))
            counts["spans"] = state.get("span_count", 0)  # the retrieval index count, for the report
            return {"written": counts}

    def _route(key: str):
        return lambda state: "end" if state.get("dead_letter") else key

    g = StateGraph(IngestionState)
    g.add_node("chunk", chunk, retry_policy=retry_policy)
    g.add_node("segment", segment, retry_policy=retry_policy)
    g.add_node("extract_clauses", extract_clauses, retry_policy=retry_policy)
    g.add_node("index_spans", index_spans)
    g.add_node("extract_graph", extract_graph, retry_policy=retry_policy)
    g.add_node("resolve", resolve)
    g.add_node("write", write)

    g.add_edge(START, "chunk")
    g.add_conditional_edges("chunk", _route("segment"), {"segment": "segment", "end": END})
    # after segmentation, fan out (parallel): clause extraction, the span index, and graph extraction
    g.add_conditional_edges(
        "segment", lambda s: "end" if s.get("dead_letter") else ["clauses", "index", "graph"],
        {"clauses": "extract_clauses", "index": "index_spans", "graph": "extract_graph", "end": END})
    g.add_edge("extract_clauses", "resolve")  # resolve joins all three parallel branches
    g.add_edge("index_spans", "resolve")
    g.add_edge("extract_graph", "resolve")
    g.add_edge("resolve", "write")
    g.add_edge("write", END)
    return g.compile()


def _print_progress(message: str) -> None:
    print(message, flush=True)


def run_corpus_ingestion(
    adapter: CorpusAdapter, ingest_graph: Any, *, link_fn: LinkFn = lambda: 0,
    progress: Callable[[str], None] = _print_progress,
) -> IngestionReport:
    """Map every document from `adapter` through the per-document `ingest_graph`, then run `link_fn`
    (party_clause_linking, KG-7) ONCE to connect parties to clauses. A dead-lettered document is recorded and
    skipped -- the corpus ingest survives one bad document. The link runs after all writes so the PARTY_TO edges
    see every contract.

    Streams `X/N` progress via `progress` (flushed print by default; pass a no-op to silence) per the CLAUDE.md
    long-running-work rule -- a corpus ingest is long-running and MUST be monitorable."""
    documents = list(adapter.documents())  # materialize so we know N up front (for X/N progress)
    total = len(documents)
    progress(f"[ingest] starting: {total} documents")

    ingested = 0
    dead_lettered: list[dict] = []
    per_document: list[dict] = []
    for i, document in enumerate(documents, 1):
        out = ingest_graph.invoke({"document": document})
        if out.get("dead_letter"):
            dead_lettered.append(out["dead_letter"])
            progress(f"[ingest] {i}/{total} {document.source_doc_id} DEAD-LETTER "
                     f"({out['dead_letter'].get('stage')}: {out['dead_letter'].get('reason')})")
            continue
        ingested += 1
        written = out.get("written", {})
        per_document.append({"source_doc_id": document.source_doc_id, "written": written})
        progress(f"[ingest] {i}/{total} {document.source_doc_id} OK "
                 f"clauses={written.get('clauses')} entities={written.get('entities')}")

    progress(f"[ingest] {ingested}/{total} written, {len(dead_lettered)} dead-lettered; linking parties (KG-7)...")
    party_links = link_fn()
    progress(f"[ingest] done: {ingested}/{total} ingested, {len(dead_lettered)} dead-lettered, "
             f"{party_links} PARTY_TO edges")
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


def _parsed_from_text(source_doc_id: str, text: str, parse_dir: Any):
    """text -> a `ParsedDocument` (one TextItem per non-blank line), cached -- so the standard `chunk()` path
    (which loads a real DoclingDocument) works from a text corpus. INGEST-REFACTOR: the shared version of the
    per-script `_build_parsed`."""
    import hashlib

    from docling_core.types.doc.document import DoclingDocument
    from docling_core.types.doc.labels import DocItemLabel

    from rag_wright.capabilities.parsing import ParsedDocument

    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    manifest_path = parse_dir / f"{source_doc_id}.{content_hash[:16]}.json"
    if not manifest_path.exists():
        doc = DoclingDocument(name=source_doc_id)
        for line in text.split("\n"):
            if line.strip():
                doc.add_text(label=DocItemLabel.TEXT, text=line)
        doc.save_as_json(manifest_path)
    return ParsedDocument(source_doc_id=source_doc_id, content_hash=content_hash, manifest_path=str(manifest_path))


class _NoSummary:
    """A no-op summarizer -- the ingest smoke targets the typed KG + entity graph, not chunk summaries."""

    def summarize(self, text: str) -> str:  # noqa: ARG002
        return ""


def production_document_ingest(store: Any, *, cache_dir: Any, registry: Any, embedder: Any = None):
    """INGEST-REFACTOR: wire the per-document stage seams to the real capabilities (the two-halves glue).
    All heavy imports are lazy. Segmentation + LegalBERT function classification is a SHARED stage feeding both
    clause extraction and the dense/sparse Span retrieval index (INGEST-REFACTOR phase 2a). `store.ensure_schema()`
    must have been called; `registry` is the EDGAR EntityRegistry; `embedder` defaults to a BGE-M3 embedder for
    the span index."""
    import hashlib
    import json
    from pathlib import Path

    from rag_wright.capabilities.disambiguation import disambiguate
    from rag_wright.capabilities.embedding import BGEM3Embedder
    from rag_wright.capabilities.entity_resolution import resolve_entities
    from rag_wright.capabilities.graph_extraction import default_extractors
    from rag_wright.capabilities.graph_storage import to_graph
    from rag_wright.capabilities.rlm_chunking import SingleCallBoundaryDiscoverer, chunk
    from rag_wright.contracts.contract_meta import ContractRecord
    from rag_wright.contracts.function import canonical_function
    from rag_wright.contracts.identifiers import ChunkId
    from rag_wright.contracts.property import ClausePropertyRecord
    from rag_wright.ontology.clause_template import Clause
    from rag_wright.spans.clause_kg_extractor import granite_clause_extractor
    from rag_wright.spans.legalbert_classifier import LegalBertFunctionClassifier
    from rag_wright.spans.segment import segment_clause, to_span_record
    from rag_wright.util.concurrent import map_concurrent

    parse_dir = Path(cache_dir) / "parsed"
    chunk_dir = Path(cache_dir) / "chunks"
    clause_cache_dir = Path(cache_dir) / "clause_extract"  # per-span granite result cache (bump/re-run safe)
    for directory in (parse_dir, chunk_dir, clause_cache_dir):
        directory.mkdir(parents=True, exist_ok=True)
    discoverer = SingleCallBoundaryDiscoverer()
    summarizer = _NoSummary()
    clause_extractor = granite_clause_extractor()
    extractors = default_extractors()
    classifier = LegalBertFunctionClassifier.load(Path("data/models/legalbert_function"), device="cpu")
    embedder = embedder if embedder is not None else BGEM3Embedder()  # BGE-M3 dense+sparse for the span index
    # the extraction cache is keyed by the Clause template's schema, so a template change (e.g. new field
    # constraints) auto-invalidates it -- a re-run re-extracts instead of serving stale records.
    template_version = hashlib.sha256(
        json.dumps(Clause.model_json_schema(), sort_keys=True).encode("utf-8")).hexdigest()[:12]

    def _chunk_id(value: str) -> ChunkId:
        source, index, content_hash = value.rsplit(":", 2)
        return ChunkId(source_doc_id=source, chunk_index=int(index), content_hash=content_hash)

    def chunk_fn(doc: SourceDocument) -> list:
        parsed = _parsed_from_text(doc.source_doc_id, doc.text, parse_dir)
        return list(chunk(parsed, summarizer=summarizer, cache_dir=chunk_dir, discoverer=discoverer).chunks)

    def segment_fn(doc: SourceDocument, chunks: list) -> list:
        # SHARED segmentation + function classification (LegalBERT, local), fed to BOTH clause extraction and the
        # span index. Every span is kept (the index needs them all); its `function` is the classifier's label,
        # canonicalized where possible (NONE / off-taxonomy stays as-is -- the ingest_cuad convention).
        ops = [(op, ch.doc_start) for ch in chunks
               for op in segment_clause(ch.chunk_id, ch.text) if op.text.strip()]
        if not ops:
            return []
        functions = classifier.classify([op.text for op, _ in ops])
        return [(op, canonical_function(raw) or raw, chunk_doc_start)
                for (op, chunk_doc_start), raw in zip(ops, functions)]

    def clauses_fn(doc: SourceDocument, segments: list) -> list:
        # Extract the typed record only for spans that classify to a REAL function (NONE / off-taxonomy spans are
        # not clauses -- but they ARE still indexed by index_fn). CONCURRENT (map_concurrent, like
        # populate_clause_kg): granite is ~10s/single-call, so sequential would be minutes per document.
        jobs = [
            (index, op, canonical_function(function))
            for index, (op, function, _cds) in enumerate(segments)
            if canonical_function(function) is not None
        ]
        if not jobs:
            return []

        def _extract(job):
            index, op, function = job
            clause_cid = ChunkId.of(doc.source_doc_id, index, op.text)
            cache_file = clause_cache_dir / (hashlib.sha256(
                f"{clause_cid.value}|{function}|{template_version}".encode("utf-8")).hexdigest()[:32] + ".json")
            if cache_file.exists():  # a prior SUCCESSFUL extraction -> reuse it, no granite re-call
                return ClausePropertyRecord.model_validate_json(cache_file.read_text(encoding="utf-8"))
            try:
                record = clause_extractor(
                    chunk_id=clause_cid, function=function, text=op.text, span_id=op.span_id)
            except Exception:  # noqa: BLE001 - a per-span failure (truncated/invalid JSON) is SKIPPED (matches
                return None    # populate_clause_kg) and NOT cached, so a template-fix re-run re-extracts it
            cache_file.write_text(record.model_dump_json(), encoding="utf-8")  # cache successes only
            return record

        return [record for record in map_concurrent(jobs, _extract, max_concurrency=8) if record is not None]

    def index_fn(doc: SourceDocument, segments: list) -> int:
        # The dense/sparse Span retrieval index (FR-R): embed every span (BGE-M3, one batch) and upsert a
        # SpanRecord with document-absolute offsets + function tag. Per-span write is best-effort (skip on error).
        if not segments:
            return 0
        dense_vecs, sparse_vecs = embedder.encode_batch([op.text.strip() for op, _, _ in segments])
        count = 0
        for (op, function, chunk_doc_start), dense, sparse in zip(segments, dense_vecs, sparse_vecs):
            try:
                store.upsert_span(to_span_record(
                    op, contract_id=doc.source_doc_id, chunk_doc_start=chunk_doc_start,
                    dense_vector=list(dense), sparse_vector=sparse, function=function))
                count += 1
            except Exception:  # noqa: BLE001 - a per-span index write must not sink the document's KG
                continue
        return count

    def graph_fn(doc: SourceDocument, chunks: list) -> list:
        jobs = [(ch, ex) for ch in chunks for ex in extractors]  # concurrent per chunk (docling-graph)

        def _extract(job):
            chunk_obj, extractor = job
            try:
                return extractor.extract(_chunk_id(chunk_obj.chunk_id), chunk_obj.text)
            except Exception:  # noqa: BLE001 - one bad extraction must not sink the document (matches GP-1B)
                return None

        return [result for result in map_concurrent(jobs, _extract, max_concurrency=8) if result is not None]

    def resolve_fn(extraction_results: list):
        resolution = resolve_entities(disambiguate(extraction_results), extraction_results, registry=registry)
        return to_graph(resolution)  # (nodes, edges)

    def write_fn(doc: SourceDocument, clause_records: list, resolution: Any) -> dict:
        store.upsert_contract(ContractRecord(
            contract_id=doc.source_doc_id, name=doc.metadata.get("raw_title", ""),
            source_doc_id=doc.source_doc_id,
            content_hash=hashlib.sha256(doc.text.encode("utf-8")).hexdigest()))
        for record in clause_records:
            store.write_clause_kg(record)
        nodes, edges = resolution
        store.write_graph(nodes, edges)
        return {"clauses": len(clause_records), "entities": len(nodes), "edges": len(edges)}

    return build_document_ingest(
        chunk_fn, segment_fn, clauses_fn, index_fn, graph_fn, resolve_fn, write_fn)


def run_cuad_ingestion(cuad_path: Any, store: Any, *, cache_dir: Any, limit: int = 0) -> IngestionReport:
    """INGEST-REFACTOR proof: ingest CUAD through the GENERIC pipeline + `CuadAdapter` -- one call, no
    `ingest_cuad()`. Builds the EDGAR registry, ensures the schema, ingests `limit` documents, and runs
    party_clause_linking (KG-7) once. Point `store` at a SCRATCH database for a non-destructive smoke."""
    import json
    from pathlib import Path

    from rag_wright.capabilities.dg_extraction import build_verified_registry
    from rag_wright.capabilities.party_clause_linking import party_clause_linking

    store.ensure_schema()
    vset = json.loads(Path("data/edgar/verification_set.json").read_text(encoding="utf-8"))
    ingest_graph = production_document_ingest(
        store, cache_dir=cache_dir, registry=build_verified_registry(vset))
    return run_corpus_ingestion(
        CuadAdapter(cuad_path, limit=limit), ingest_graph,
        link_fn=lambda: len(party_clause_linking(store).links))


def register_contract_ingestion_pipeline(registry) -> None:
    """LG-3d: register `contract_ingestion_pipeline` (composite subgraph; source docs -> populated contract KG)."""
    registry.register(
        "contract_ingestion_pipeline",
        contract=IngestionReport,
        kind="subgraph",
        display_name="Contract ingestion pipeline (corpus -> populated, connected KG)",
    )
