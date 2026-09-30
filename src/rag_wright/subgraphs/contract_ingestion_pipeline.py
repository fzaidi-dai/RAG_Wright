"""LG-3d: `contract_ingestion_pipeline` -- the GENERIC ingestion pipeline as a composite LangGraph subgraph.

Source documents -> a populated, connected contract KG, composing the LG-1/LG-2 ingest subgraphs + the
ingest-side capabilities. The pipeline is corpus-AGNOSTIC; each per-document ingest runs:

        START --> chunk [RetryPolicy]        (semantic_chunking: text -> chunks)
                    v
                 segment                      (SHARED: segment + BATCHED LLM function-classify -> spans; ADR-0048)
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
dead-letters). (The KG-7 `party_clause_linking`/PartyTo post-step was retired -- issue 0028 / ADR-0091 --
since party->clause is reached via CONTRACTS_WITH provenance + the contract-scoped clause KG.)

Every stage is dependency-injected so the graph is hermetically testable with stubs -- no live LLM/store.
`production_contract_ingestion_pipeline` wires the real capabilities. The `source_doc_id` on every
`SourceDocument` MUST come from `canonical_source_doc_id` (HYG-1), so every graph shares one id scheme and the
KG-7 link is a clean join.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional, Protocol, TypedDict, runtime_checkable

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from pydantic import BaseModel

from rag_wright.capabilities.parsing import ParsedDocument
from rag_wright.subgraphs.scaffold import DEFAULT_RETRY, business_span, dead_letter
from rag_wright.subgraphs.typed_clause_extraction import TransientExtraction


class SourceDocument(BaseModel):
    """One document to ingest: its canonical `source_doc_id` (HYG-1), its already-parsed text, and optional
    per-corpus metadata (e.g. CUAD annotated parties, ACORD pre-segmented spans) the stages may consult."""

    source_doc_id: str
    text: str
    metadata: dict = {}
    # CHUNK-7 (ADR-0058, issue 0004): the REAL docling parse (structure preserved), set by a BYTE-source adapter
    # (or `parsed_source_document`). When present, the chunk stage uses it so the structural pass fires on the
    # document's actual headings; when None (genuinely text-only input) the chunker falls back to a text parse.
    parsed: Optional[ParsedDocument] = None
    # 0009-WIRE2: pages the tiered OCR could not read even after VLM escalation (a degraded scan) -- surfaced as an
    # `ocr` PARTIAL in the IngestionReport, never silently ingested as gibberish.
    ocr_unreadable_pages: list[int] = []


class IngestionReport(BaseModel):
    """The composite's output: how many documents were ingested and which dead-lettered (with reasons)."""

    documents_ingested: int
    dead_lettered: list[dict]
    party_links: int = 0  # DEPRECATED (issue 0028 / ADR-0091): the PartyTo layer was retired; always 0. Kept a
    #                       release so a consumer reading this field does not break; slated for removal.
    per_document: list[dict]
    # PROD-3 lossless invariant (ADR-0050): documents written but INCOMPLETE (>=1 clause extraction failed after
    # retries, OR >=1 span-index write failed -- 0006-C). Surfaced here so a partial is KNOWN at job completion,
    # never discovered later by grepping logs. Each entry:
    #   {"source_doc_id": str,
    #    "failures": [{"kind": "clause"|"span", "span_id": str, "reason": str}, ...],  # ENG-1: read THIS -- always
    #                                                                                   #  present, covers BOTH kinds
    #    "clause_failures": [...],   # present only if a clause loss (back-compat)
    #    "span_failures":   [...]}   # present only if a span loss   (back-compat)
    # Integrators: key on `failures` (or on the doc being in `partial` at all). Reading only `clause_failures`
    # SILENTLY misses a span-only loss -- the per-kind keys are optional, `failures` is not.
    partial: list[dict] = []


@runtime_checkable
class CorpusAdapter(Protocol):
    """The ONE per-corpus seam: yield the corpus's documents as `SourceDocument`s (parsing + canonical id +
    any corpus metadata live here). Everything downstream is corpus-agnostic."""

    def documents(self) -> Iterable[SourceDocument]: ...


# The injected per-document stage seams (each wraps a built subgraph / capability; stubbed in tests).
ChunkFn = Callable[[SourceDocument], list]  # doc -> chunks
SegmentFn = Callable[[SourceDocument, list], list]  # (doc, chunks) -> [(op, primary_function, chunk_doc_start, scores)]
ClausesFn = Callable[[SourceDocument, list], Any]  # (doc, segments) -> typed clause records (list) OR
# {clause_records, clause_failures} when the adapter reports per-clause failures (PROD-3 lossless; extract_clauses
# accepts either shape for back-compat)
IndexFn = Callable[[SourceDocument, list], int]  # (doc, segments) -> #Span records written (retrieval index)
GraphFn = Callable[[SourceDocument, list], list]  # (doc, chunks) -> ExtractionResults
ResolveFn = Callable[[list], Any]  # extraction results -> resolution
WriteFn = Callable[[SourceDocument, list, Any], dict]  # (doc, clause records, resolution) -> counts
LinkFn = Callable[[], int]  # corpus-level post-ingest hook -> an int count (default no-op). The KG-7 PartyTo
#                             provider (`corpus_party_link_fn`) was retired (issue 0028 / ADR-0091); the seam
#                             stays for signature stability + a future corpus-level pass, default `lambda: 0`.


class IngestionState(TypedDict, total=False):
    document: SourceDocument
    chunks: list
    segments: list  # shared: [(OperativeSpan, primary_function, chunk_doc_start, [FunctionScore])] (ADR-0048)
    clause_records: list
    clause_failures: list  # PROD-3 lossless: per-clause extraction failures (span_id + reason) -> doc flagged PARTIAL
    span_count: int  # Span records written to the retrieval index (best-effort)
    span_failures: list  # 0006-C (NFR-2): per-span index-write failures (span_id + reason) -> doc flagged PARTIAL
    extraction_results: list
    resolution: Any
    written: dict
    dead_letter: Optional[dict]




def _wire_ingest_graph(chunk, segment, extract_clauses, index_spans, extract_graph, resolve, write,
                       retry_policy) -> Any:
    """The per-document ingest graph topology (shared by the sync `build_document_ingest` and the async
    `abuild_document_ingest`). LangGraph `add_node` accepts sync OR async node functions, so the wiring is
    identical -- only the node functions differ (sync `.invoke` vs async `.ainvoke`)."""
    def _route(key: str):
        return lambda state: "end" if state.get("dead_letter") else key

    g = StateGraph(IngestionState)
    g.add_node("chunk", chunk, retry_policy=retry_policy)
    g.add_node("segment", segment, retry_policy=retry_policy)
    g.add_node("extract_clauses", extract_clauses, retry_policy=retry_policy)
    g.add_node("index_spans", index_spans)
    g.add_node("extract_graph", extract_graph, retry_policy=retry_policy)
    g.add_node("resolve", resolve)
    g.add_node("write", write, retry_policy=retry_policy)

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


def abuild_document_ingest(
    chunk_fn: Any, segment_fn: Any, clauses_fn: Any, index_fn: Any, graph_fn: Any, resolve_fn: Any,
    write_fn: Any, *, retry_policy: Any = DEFAULT_RETRY,
):
    """ASYNC-B2e (ADR-0057): the async per-document ingest subgraph. Same topology + dead-letter/retry semantics
    as `build_document_ingest`, but the nodes are `async def` and the injected stage fns are awaited -- so the
    model calls run on the async seam (true wall-clock deadline) and the parallel branches (clauses/index/graph)
    run concurrently on the event loop. Invoke via `ainvoke`."""
    max_attempts = int(getattr(retry_policy, "max_attempts", 3))

    async def _aguard(name: str, work: Any, runtime: Runtime, doc: SourceDocument) -> dict:
        attempt = runtime.execution_info.node_attempt
        with business_span(f"contract_ingestion.{name}", source_doc_id=doc.source_doc_id):
            try:
                return await work()
            except Exception as exc:  # noqa: BLE001 - transient -> retry, or dead-letter on exhaustion
                if attempt >= max_attempts:
                    return {"dead_letter": dead_letter(
                        "ingest_failed", source_doc_id=doc.source_doc_id, stage=name, error=str(exc))}
                raise TransientExtraction(str(exc)) from exc

    async def chunk(state: IngestionState, runtime: Runtime) -> IngestionState:
        doc = state["document"]

        async def _w() -> dict:
            return {"chunks": await chunk_fn(doc)}

        return await _aguard("chunk", _w, runtime, doc)

    async def segment(state: IngestionState, runtime: Runtime) -> IngestionState:
        doc = state["document"]

        async def _w() -> dict:
            return {"segments": await segment_fn(doc, state.get("chunks", []))}

        return await _aguard("segment", _w, runtime, doc)

    async def extract_clauses(state: IngestionState, runtime: Runtime) -> IngestionState:
        doc = state["document"]

        async def _w() -> dict:
            result = await clauses_fn(doc, state.get("segments", []))
            if isinstance(result, dict):
                return {"clause_records": result.get("clause_records", []),
                        "clause_failures": result.get("clause_failures", [])}
            return {"clause_records": result}

        return await _aguard("extract_clauses", _w, runtime, doc)

    async def index_spans(state: IngestionState) -> IngestionState:
        if state.get("dead_letter"):
            return {}
        doc = state["document"]
        with business_span("contract_ingestion.index_spans"):
            try:
                res = await index_fn(doc, state.get("segments", []))
            except Exception as exc:  # noqa: BLE001 - best-effort index: never dead-letters, but the loss is VISIBLE
                return {"span_count": 0,
                        "span_failures": [{"span_id": "*", "reason": f"index node failed: {exc!r}"}]}
            if isinstance(res, dict):  # 0006-C: richer return surfaces per-span write failures (else a bare count)
                return {"span_count": res.get("span_count", 0),
                        "span_failures": res.get("span_failures", [])}
            return {"span_count": res}

    async def extract_graph(state: IngestionState, runtime: Runtime) -> IngestionState:
        doc = state["document"]

        async def _w() -> dict:
            return {"extraction_results": await graph_fn(doc, state.get("chunks", []))}

        return await _aguard("extract_graph", _w, runtime, doc)

    async def resolve(state: IngestionState) -> IngestionState:
        if state.get("dead_letter"):
            return {}
        with business_span("contract_ingestion.resolve"):
            return {"resolution": await resolve_fn(state.get("extraction_results", []))}

    async def write(state: IngestionState, runtime: Runtime) -> IngestionState:
        if state.get("dead_letter"):
            return {}
        doc = state["document"]

        async def _do() -> dict:
            counts = await write_fn(doc, state.get("clause_records", []), state.get("resolution"))
            counts["spans"] = state.get("span_count", 0)
            return {"written": counts}

        return await _aguard("write", _do, runtime, doc)

    return _wire_ingest_graph(chunk, segment, extract_clauses, index_spans, extract_graph, resolve, write,
                              retry_policy)


@dataclass
class PendingDocument:
    """0009-ASYNC-INGEST: a document whose parse (incl. tiered OCR + the VLM escalation, the slowest call in the
    pipeline) is DEFERRED. The async ingest parses it CONCURRENTLY and deadline-bounded PER DOCUMENT -- so a slow
    scan on one document never blocks the loop or serializes the others -- instead of parsing every document
    synchronously upfront. The GCS adapter yields these; a pre-parsed `SourceDocument` is used as-is."""

    source_doc_id: str
    parse: Callable[[], SourceDocument]  # deferred: downloads + parses when called (run off-loop via to_thread)
    metadata: dict


_INGEST_PARSE_DEADLINE_S = 600.0  # per-document parse ceiling (a degraded multi-page doc escalated to the VLM)


async def aparse_pending(pending: PendingDocument, *, deadline_s: float = _INGEST_PARSE_DEADLINE_S) -> SourceDocument:
    """Run a `PendingDocument`'s deferred parse OFF the event loop (`to_thread`) under a wall-clock deadline
    (ADR-0057), so the tiered OCR escalation is concurrency-safe and bounded during ingestion."""
    async with asyncio.timeout(deadline_s):
        sd = await asyncio.to_thread(pending.parse)
    return sd.model_copy(update={"metadata": {**pending.metadata, **sd.metadata}})


def build_partial_entry(source_doc_id: str, clause_failures: list, span_failures: list,
                        ocr_failures: Optional[list] = None) -> Optional[dict]:
    """The single PARTIAL-entry shape, shared by the blocking driver AND the async job runner so the two can
    never drift. A UNIFIED, always-present `failures` list (kind-tagged) lets an integrator read ONE field and
    never silently miss a span-only loss; the per-kind `clause_failures`/`span_failures` keys stay for
    back-compat. Returns None when the document is fully complete (no loss -> not partial). Does not mutate the
    input lists.

    STABLE PUBLIC API (ENG-1/ENG-2): the product imports this helper and depends on its signature, this import
    path, and the `failures` entry shape. Pinned by `tests/subgraphs/test_partial_entry_contract.py`. FORWARD-COMPAT
    RULE: a new loss kind is a new `kind` value inside `failures` (0009-WIRE2 adds `ocr` -- a page a degraded scan
    left unreadable), NEVER a replacement top-level key -- so an integrator counting the kind-tagged list keeps
    surfacing losses it has no dedicated field for. `ocr_failures` is a new OPTIONAL trailing arg (3-arg callers
    are unaffected)."""
    clause_failures = clause_failures or []
    span_failures = span_failures or []
    ocr_failures = ocr_failures or []
    if not (clause_failures or span_failures or ocr_failures):
        return None
    failures = ([{"kind": "clause", **f} for f in clause_failures]
                + [{"kind": "span", **f} for f in span_failures]
                + [{"kind": "ocr", **f} for f in ocr_failures])
    entry: dict = {"source_doc_id": source_doc_id, "failures": failures}
    if clause_failures:
        entry["clause_failures"] = clause_failures
    if span_failures:
        entry["span_failures"] = span_failures
    if ocr_failures:
        entry["ocr_failures"] = ocr_failures
    return entry


def _print_progress(message: str) -> None:
    print(message, flush=True)




async def arun_corpus_ingestion(
    adapter: CorpusAdapter, ingest_graph: Any, *, link_fn: LinkFn = lambda: 0,
    progress: Callable[[str], None] = _print_progress,
    is_done: Callable[[SourceDocument], bool] = lambda _doc: False,
    job_id: str | None = None,
) -> IngestionReport:
    """ASYNC-B2e (ADR-0057): the async twin of `run_corpus_ingestion`. Maps each document through the ASYNC
    per-document `ingest_graph` via `ainvoke` -- so the model calls carry the true wall-clock deadline and the
    per-document parallel branches run concurrently on the loop. Same X/N progress, resume-skip, dead-letter, and
    partial semantics as the sync driver (documents are processed sequentially; intra-document parallelism comes
    from the graph).

    Observability (issue 0017): each document's ingest runs inside `traced_run`, so EVERY generation it emits is
    stamped with the correlation id -- `document_id = source_doc_id` and the caller's `job_id` -- making cost per
    document (or per job) a single Langfuse query. A no-op unless RAG_TRACE_LEVEL is on + Langfuse configured."""
    from rag_wright.models.tracing import traced_run
    documents = list(adapter.documents())
    total = len(documents)
    progress(f"[ingest] starting: {total} documents")

    ingested = skipped = 0
    dead_lettered: list[dict] = []
    partial: list[dict] = []
    per_document: list[dict] = []
    for i, item in enumerate(documents, 1):
        if is_done(item):
            ingested += 1
            skipped += 1
            if skipped % 25 == 0 or i == total:
                progress(f"[ingest] {i}/{total} resume-skipping already-done docs ({skipped} skipped so far)")
            continue
        try:  # 0009-ASYNC-INGEST: parse a deferred doc off-loop + deadline-bounded (no upfront-sync block)
            document = await aparse_pending(item) if isinstance(item, PendingDocument) else item
        except Exception as exc:  # noqa: BLE001 - a parse-timeout/crash dead-letters THAT doc, never the run
            dead_lettered.append({"source_doc_id": item.source_doc_id, "stage": "parse",
                                  "reason": "parse_failed", "error": str(exc)[:200]})
            progress(f"[ingest] {i}/{total} {item.source_doc_id} DEAD-LETTER (parse: {str(exc)[:80]})")
            continue
        with traced_run(document_id=document.source_doc_id, job_id=job_id, name="ingest_document"):
            out = await ingest_graph.ainvoke({"document": document})
        if out.get("dead_letter"):
            dead_lettered.append(out["dead_letter"])
            progress(f"[ingest] {i}/{total} {document.source_doc_id} DEAD-LETTER "
                     f"({out['dead_letter'].get('stage')}: {out['dead_letter'].get('reason')})")
            continue
        ingested += 1
        written = out.get("written", {})
        per_document.append({"source_doc_id": document.source_doc_id, "written": written})
        summary = " ".join(f"{k}={v}" for k, v in written.items()) or "ok"
        clause_failures = out.get("clause_failures") or []
        span_failures = out.get("span_failures") or []
        ocr_failures = [{"page": pg, "reason": "unreadable scan (OCR + VLM failed)"}  # 0009-WIRE2
                        for pg in (getattr(document, "ocr_unreadable_pages", None) or [])]
        entry = build_partial_entry(document.source_doc_id, clause_failures, span_failures, ocr_failures)
        if entry is not None:  # 0006-C / 0009: ANY kind of loss flags the doc PARTIAL (never silent)
            partial.append(entry)
            reasons = ", ".join(
                p for p in (f"{len(clause_failures)} clause(s)" if clause_failures else "",
                            f"{len(span_failures)} span(s)" if span_failures else "",
                            f"{len(ocr_failures)} unreadable page(s)" if ocr_failures else "") if p)
            progress(f"[ingest] {i}/{total} {document.source_doc_id} PARTIAL ({reasons} failed) {summary}")
        else:
            progress(f"[ingest] {i}/{total} {document.source_doc_id} OK {summary}")

    party_links = link_fn()  # default no-op (issue 0028: PartyTo retired); a caller may still pass a corpus-level hook
    progress(f"[ingest] done: {ingested}/{total} ingested ({skipped} resume-skipped), "
             f"{len(dead_lettered)} dead-lettered, {len(partial)} partial")
    return IngestionReport(
        documents_ingested=ingested, dead_lettered=dead_lettered,
        party_links=party_links, per_document=per_document, partial=partial)


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


def _parsed_for(doc: SourceDocument, parse_dir: Any) -> ParsedDocument:
    """CHUNK-7 (ADR-0058): the ParsedDocument the chunker chunks. Use the document's REAL docling parse
    (`doc.parsed`, structure preserved) when a byte-source adapter provided one -- so the structural pass fires
    on the document's own headings; otherwise fall back to a text-only parse of `doc.text` (genuinely
    structureless input, which the chunker's tag-parse fallback handles). This is what carries docling structure
    to the chunker."""
    if doc.parsed is not None:
        return doc.parsed
    return _parsed_from_text(doc.source_doc_id, doc.text, parse_dir)


def _attach_page_provenance(doc: SourceDocument, chunks: list, segments: list, parse_dir: Any) -> list:
    """issue 0032 (CU-B5): enrich each segment's `OperativeSpan` with its source page(s) + best-effort bbox.

    Builds a page<->char map once from the parsed document's per-item `prov` pages and the canonical text, then
    looks up each span's canonical range (`chunk_doc_start + op.start/end`). Deterministic, no model call.
    Best-effort by design: any failure, a parse with no page provenance (the text-only ingest leg), or a chunk
    with no `doc_start` leaves the span's `pages` empty -- the honest 'no page' fallback, never a broken ingest."""
    if not segments:
        return segments
    try:
        from rag_wright.capabilities.parsing import load_document
        from rag_wright.capabilities.rlm_chunking import canonical_document_text
        from rag_wright.corpus.document_parser import content_items
        from rag_wright.spans.page_map import build_page_offset_map, pages_for

        page_map = build_page_offset_map(
            content_items(load_document(_parsed_for(doc, parse_dir))), canonical_document_text(chunks))
    except Exception:  # noqa: BLE001 - provenance is best-effort; never fail an ingest over a page lookup
        return segments
    if not page_map:
        return segments
    enriched: list = []
    for (op, function, chunk_doc_start, scores) in segments:
        if chunk_doc_start is None:
            enriched.append((op, function, chunk_doc_start, scores))
            continue
        pages, bbox = pages_for(page_map, chunk_doc_start + op.start, chunk_doc_start + op.end)
        enriched.append((op.model_copy(update={"pages": pages, "bbox": bbox}), function, chunk_doc_start, scores))
    return enriched


def parsed_source_document(
    source_doc_id: str, name: str, data: bytes, *, cache_dir: Any, metadata: Optional[dict] = None
) -> SourceDocument:
    """Build a STRUCTURE-BEARING `SourceDocument` from raw document BYTES (PDF/DOCX/HTML/MD): docling-parse ONCE
    (content-hash gated + cached), carry the `DoclingDocument` on `.parsed` (so the chunker's structural pass
    fires on real headings), and set `.text` to the flattened text (for the text-consuming stages). This is how a
    byte-source corpus adapter -- or the product (RuleWright), which hand-builds its ingest -- feeds a real
    document to the engine; a plain-text `SourceDocument` (no `.parsed`) still uses the text fallback."""
    import hashlib
    import json
    from pathlib import Path

    from rag_wright.capabilities.parsing import TieredOCRParser, load_document
    from rag_wright.corpus.document_parser import document_to_text, parse_document_bytes

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    content_hash = hashlib.sha256(data).hexdigest()
    manifest_path = cache_dir / f"{source_doc_id}.{content_hash[:16]}.json"
    ocr_sidecar = cache_dir / f"{source_doc_id}.{content_hash[:16]}.ocr.json"  # the OCR verdict, cached alongside
    parsed = ParsedDocument(
        source_doc_id=source_doc_id, content_hash=content_hash, manifest_path=str(manifest_path))
    if manifest_path.exists():  # content-hash gate: parse once (restore the OCR verdict from the sidecar)
        document = load_document(parsed)
        unreadable = json.loads(ocr_sidecar.read_text()) if ocr_sidecar.exists() else []
    else:  # 0009-WIRE2: tiered OCR -- capture which pages stayed unreadable even after VLM, so they surface PARTIAL
        tiered = TieredOCRParser()
        document = parse_document_bytes(name, data, parser=tiered)
        document.save_as_json(manifest_path)
        unreadable = list(tiered.report.unreadable_pages)
        ocr_sidecar.write_text(json.dumps(unreadable))
    return SourceDocument(
        source_doc_id=source_doc_id, text=document_to_text(document), parsed=parsed, metadata=metadata or {},
        ocr_unreadable_pages=unreadable)


async def aparsed_source_document(
    source_doc_id: str, name: str, data: bytes, *, cache_dir: Any, metadata: Optional[dict] = None,
    deadline_s: float = _INGEST_PARSE_DEADLINE_S,
) -> SourceDocument:
    """The ASYNC, deadline-bounded twin of `parsed_source_document` (ADR-0057) -- STABLE PUBLIC API. Runs the sync
    build (docling parse + the tiered OCR/VLM escalation, the slowest call in the pipeline) OFF the event loop
    (`to_thread`) under an `asyncio.timeout`, so a hand-built async ingest can parse a document into the
    structure-bearing `SourceDocument` the chunker needs WITHOUT reimplementing the wrapper (or blocking the loop).
    Same caveat as every `to_thread` bound: the deadline unblocks the CALLER; the docling worker thread finishes in
    the background (true cancellation would route the vision call through the async model seam)."""
    async with asyncio.timeout(deadline_s):
        return await asyncio.to_thread(
            parsed_source_document, source_doc_id, name, data, cache_dir=cache_dir, metadata=metadata)


class _NoSummary:
    """A no-op summarizer -- the ingest smoke targets the typed KG + entity graph, not chunk summaries."""

    def summarize(self, text: str) -> str:  # noqa: ARG002
        return ""


def seed_party_cache(party_dir: Any, legacy_path: Any) -> int:
    """INGEST-REFACTOR (a): pre-populate the per-contract party cache from GP-1B's `dg_extracted_parties.json`
    (a `{raw_title: [party names]}` map) so a full ingest REUSES those ~482 extractions instead of re-calling
    granite. The legacy key is the raw title; it is canonicalized (HYG-1) to match the pipeline's
    `source_doc_id`. Idempotent: never overwrites an existing (possibly fresher) entry. Returns #seeded."""
    import json
    from pathlib import Path

    from rag_wright.contracts.identifiers import canonical_source_doc_id

    if not Path(legacy_path).exists():
        return 0
    legacy = json.loads(Path(legacy_path).read_text(encoding="utf-8"))
    Path(party_dir).mkdir(parents=True, exist_ok=True)
    seeded = 0
    for raw_title, names in legacy.items():
        cache_file = Path(party_dir) / f"{canonical_source_doc_id(raw_title)}.json"
        if not cache_file.exists():
            cache_file.write_text(json.dumps(names), encoding="utf-8")
            seeded += 1
    return seeded


def seed_chunk_cache(chunk_dir: Any, legacy_chunk_dir: Any) -> int:
    """INGEST-REFACTOR (a): copy existing `chunk()` manifests into the run's chunk cache so a full ingest skips
    re-chunking already-chunked documents. The manifest name embeds the content hash, so a copied manifest is
    only ever REUSED when the pipeline's text hashes to the same key (a text change misses, as it must).
    Idempotent (skips existing). Returns #copied."""
    import shutil
    from pathlib import Path

    if not Path(legacy_chunk_dir).exists():
        return 0
    Path(chunk_dir).mkdir(parents=True, exist_ok=True)
    copied = 0
    for manifest in Path(legacy_chunk_dir).glob("*.chunks.json"):
        dest = Path(chunk_dir) / manifest.name
        if not dest.exists():
            shutil.copyfile(manifest, dest)
            copied += 1
    return copied


def per_contract_graph_extraction(doc: SourceDocument, *, party_dir: Any, names_fn: Callable[[str], list]) -> list:
    """INGEST-REFACTOR (a) / GP-1B (ADR-0035): extract the signing parties ONCE PER CONTRACT, not per chunk.
    Parties are named once in the preamble (the extractor bounds to `_DEFAULT_PREAMBLE_CHARS`), so one call per
    contract is both the proven-fidelity design AND ~10x cheaper than the former per-chunk fan-out. Reuses cached
    party names (seeded from `dg_extracted_parties.json` or a prior run) when present, else calls `names_fn` and
    caches the result. `parties_to_extraction` rebuilds the exact `ExtractionResult` the live extractor would
    (its own body is `names = [p.name for p in parties]; parties_to_extraction(...)`), so the cache is lossless.
    Returns `[ExtractionResult]` (empty when no parties)."""
    cache_file = _party_cache_file(party_dir, doc)
    names = _cached_party_names(cache_file)
    if names is None:  # not cached -> extract once, then cache (empty results are cached too, as before)
        names = names_fn(doc.text)
        _write_party_cache(cache_file, names)
    return _parties_extraction(names, doc)


async def aper_contract_graph_extraction(
    doc: SourceDocument, *, party_dir: Any, anames_fn: Callable[[str], Any],
    affil_dir: Any = None, aaffiliations_fn: Optional[Callable[[str], Any]] = None) -> list:
    """ASYNC-B2c (ADR-0057): the async twin of `per_contract_graph_extraction`. The party-names model call runs on
    the async seam via `anames_fn` (true wall-clock deadline); the cache and `parties_to_extraction` are sync.

    issue 0027: when `aaffiliations_fn` + `affil_dir` are wired, ALSO extract corporate affiliations from the same
    preamble (its own model call, separately cached, lexically pre-filtered), appending `AFFILIATE_OF` facts. Off
    (params None) -> parties only, unchanged."""
    cache_file = _party_cache_file(party_dir, doc)
    names = _cached_party_names(cache_file)
    if names is None:
        names = await anames_fn(doc.text)
        _write_party_cache(cache_file, names)
    results = _parties_extraction(names, doc)
    if aaffiliations_fn is not None and affil_dir is not None:
        results = results + await _aaffiliations_extraction(doc, affil_dir, aaffiliations_fn)
    return results


async def _aaffiliations_extraction(doc: SourceDocument, affil_dir: Any, aaffiliations_fn: Callable[[str], Any]) -> list:
    """issue 0027: extract (or reuse cached) corporate-affiliation pairs for a contract, and rebuild the
    `AFFILIATE_OF` ExtractionResult. Separate cache from parties (own file), so re-ingest never re-extracts."""
    cache_file = _affil_cache_file(affil_dir, doc)
    affiliations = _cached_json(cache_file)
    if affiliations is None:
        affiliations = list(await aaffiliations_fn(doc.text))
        _write_json(cache_file, affiliations)
    if not affiliations:
        return []
    from rag_wright.capabilities.graph_extraction import affiliations_to_extraction
    from rag_wright.contracts.identifiers import ChunkId

    return [affiliations_to_extraction(ChunkId.of(doc.source_doc_id, 0, doc.text), affiliations)]


def _affil_cache_file(affil_dir: Any, doc: SourceDocument) -> Any:
    from pathlib import Path

    return Path(affil_dir) / f"{doc.source_doc_id}.json"


def _cached_json(cache_file: Any) -> Optional[list]:
    """The cached value (any JSON list) for a contract, or None when there is no cache entry (distinct from a
    cached EMPTY result `[]`). Shared by the party and affiliation caches."""
    import json

    return json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else None


def _write_json(cache_file: Any, value: list) -> None:
    import json

    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(value), encoding="utf-8")


def _party_cache_file(party_dir: Any, doc: SourceDocument) -> Any:
    from pathlib import Path

    return Path(party_dir) / f"{doc.source_doc_id}.json"


def _cached_party_names(cache_file: Any) -> Optional[list]:
    """The cached party names for a contract, or None when there is no cache entry (distinct from a cached
    EMPTY result, which is `[]`)."""
    import json

    return json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else None


def _write_party_cache(cache_file: Any, names: list) -> None:
    import json

    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(names), encoding="utf-8")


def _parties_extraction(names: list, doc: SourceDocument) -> list:
    if not names:
        return []
    from rag_wright.capabilities.graph_extraction import parties_to_extraction
    from rag_wright.contracts.identifiers import ChunkId

    return [parties_to_extraction(ChunkId.of(doc.source_doc_id, 0, doc.text), names)]




async def _asegment_and_classify(chunks: list, classify_fn: Any, *, segment: Any = None,
                                 max_concurrency: int | None = None) -> list:
    """ASYNC-B2e (ADR-0057): the async twin of `_segment_and_classify` -- classify each chunk's spans via the
    async classifier (`aclassify_spans`, true wall-clock deadline). Segmentation (`segment_clause`) is CPU/regex,
    kept sync.

    CLASSIFY-CONCURRENCY-1: chunks are classified CONCURRENTLY (`asyncio.gather`), not one-after-another -- the
    earlier `for ch: await ...` serialized M chunks into M network round-trips for no reason (nothing depends on
    chunk order). ONE shared semaphore, threaded into every `aclassify_spans`, bounds the TOTAL in-flight
    sub-batch LLM calls across all chunks to a single deliberate knob (`CLASSIFY_CONCURRENCY`, default 8) -- it is
    acquired only at the leaf call, so the outer gather cannot deadlock. `gather` preserves order, so the flattened
    output is identical to the sequential version, only faster."""
    import os

    from rag_wright.contracts.function import NO_FUNCTION, primary_function

    seg = segment
    if seg is None:
        from rag_wright.spans.segment import segment_clause

        seg = segment_clause
    # segment (CPU/regex, sync) -> per-chunk operative spans, dropping empty chunks
    per_chunk = [(ch, ops) for ch in chunks
                 if (ops := [op for op in seg(ch.chunk_id, ch.text) if op.text.strip()])]
    if not per_chunk:
        return []
    n = max_concurrency if max_concurrency is not None else int(os.environ.get("CLASSIFY_CONCURRENCY", "8"))
    sem = asyncio.Semaphore(n)  # ONE shared bound on total in-flight classify calls (leaf-acquired -> no deadlock)
    scores_by_chunk = await asyncio.gather(
        *(classify_fn.aclassify_spans(ch.text, [op.text for op in ops], sem=sem) for ch, ops in per_chunk))
    out: list = []
    for (ch, ops), scores_per_span in zip(per_chunk, scores_by_chunk):  # gather preserves order -> stable output
        for op, scores in zip(ops, scores_per_span):
            out.append((op, primary_function(scores) or NO_FUNCTION, ch.doc_start, scores))
    return out


def clause_extraction_jobs(segments: list) -> list:
    """Group ordered `segments` into PROVISIONS and emit one clause-extraction job per provision, as
    `[(index, anchor_op, function, scores, text)]`: `text` is the provision's merged span text (what the extractor
    reads), `anchor_op` is its first span (the citation anchor + provenance), `index` is the provision ordinal.

    Issue 0038: a `Clause` is a PROVISION, not a sentence. Extracting per span made 98% of spans clauses (a clause
    per sentence) once issue 0036 removed the function gate -- the gate had been doing accidental provision
    detection. The provision unit is the numbered section (`spans.segment.starts_new_provision`); retrieval stays
    per span (`index_fn` is unchanged). A provision boundary is a CHUNK change OR a heading span, so granularity
    self-adjusts: numbered sections -> provision-level; a heading-less document -> chunk-level (never per sentence,
    never one clause per document).

    Within a provision, `is_extractable_span` still drops furniture spans (page numbers, signature/notice labels)
    from the merged text; a provision that is ALL furniture yields no clause. The FUNCTION stays a soft tag
    (ADR-0082, issue 0036): the provision's function is the first non-NONE among its spans, else NO_FUNCTION -- an
    untagged provision is still extracted (function-independent extraction).
    """
    from rag_wright.contracts.function import NO_FUNCTION
    from rag_wright.spans.segment import is_extractable_span, starts_new_provision

    # 1) group consecutive segments into provisions (boundary = chunk change OR a provision-heading span)
    groups: list[list] = []
    for seg in segments:
        op = seg[0]
        if groups and op.parent_chunk_id == groups[-1][-1][0].parent_chunk_id and not starts_new_provision(op.text):
            groups[-1].append(seg)
        else:
            groups.append([seg])

    # 2) one job per provision, over its EXTRACTABLE spans only (furniture dropped; all-furniture -> no clause)
    jobs: list = []
    index = 0
    for group in groups:
        members = [seg for seg in group if is_extractable_span(seg[0].text)]
        if not members:
            continue
        anchor_op = members[0][0]
        text = "\n".join(seg[0].text.strip() for seg in members)
        function = next((fn for (_op, fn, _cds, _sc) in members if fn and fn != NO_FUNCTION), NO_FUNCTION)
        scores = members[0][3]
        jobs.append((index, anchor_op, function, scores, text))
        index += 1
    return jobs


async def _aextract_clause_with_retry(
    extractor: Any, *, chunk_id: Any, function: str, text: str, span_id: str, attempts: int,
    functions: tuple[str, ...] = ()
) -> tuple[Any, str]:
    """Extract one clause with bounded retries. PARTIAL-CAUSE-1: docling-graph's `ExtractionFailed` is raised on
    ANY logged docling error -- not only a deterministic "No valid JSON", but also TRANSIENT blips (an LLM empty
    response, a gleaning failure, a rate-limit, a timeout). So EVERY failure is retried (the transient is what the
    retry recovers); an earlier EXTRACT-GUARD-1 attempt to skip retrying `ExtractionFailed` turned recoverable
    blips into lost clauses. The furniture that used to hard-fail deterministically is filtered UPSTREAM by
    `is_extractable_span`, so this loop no longer retry-storms on non-clauses. Returns `(record, "")` on success or
    `(None, reason)` on persistent failure."""
    reason = ""
    for _attempt in range(attempts):
        try:
            record = await extractor.aextract(chunk_id=chunk_id, function=function, text=text, span_id=span_id,
                                              functions=functions)
            return record, ""
        except Exception as exc:  # noqa: BLE001 - retry any failure (ExtractionFailed captures transients too)
            reason = str(exc)
    return None, reason


def aproduction_document_ingest(
    store: Any, *, cache_dir: Any, registry: Any, embedder: Any = None, party_seed_path: Any = None,
    classify_fn: Any = None, extract_model: Any = None, list_model: Any = None, samples: Any = None,
    graph_extract_model: Any = None, judge_model: Any = None, chunk_model: Any = None):
    """ASYNC-B2e (ADR-0057): the async twin of `production_document_ingest`. Wires the ASYNC stage seams (achunk,
    aclassify_spans, clause_extractor.aextract, aper_contract_graph_extraction) so the ingest model calls run on
    the async seam with the true wall-clock deadline; CPU/store work (embed, resolve, DB writes) runs off the loop
    via `asyncio.to_thread`. Clause extraction is bounded-concurrent via `asyncio.gather` + a `Semaphore`. Returns
    an ASYNC per-document graph -- drive it with `arun_corpus_ingestion`.

    Model configuration (mirrors the query/compliance entrypoints -- a caller no longer has to reach for env
    vars to change the ingest models):
      - `extract_model`: the PRIMARY clause-property extraction model -- an `ExtractionModel` OR a bare model-id
        string (wrapped via `default_extraction_model`). `None` keeps the backend default (granite, per
        `RAG_SERVING`). This is the model that produces the typed clause properties.
      - `list_model`: the SECOND model for the cross-model UNION on the LIST-bearing groups only (carve_out /
        covered_subject / damage_type). granite and gemma under-enumerate DIFFERENT list items, so their union
        is more complete than either alone; the second model is cost-scoped to list groups. A bare model-id
        string, `"off"` to disable, or `None` for the default (gemma, `RAG_INGEST_LIST_MODEL`). If you override
        `extract_model` (e.g. to qwen), set `list_model` deliberately -- the union's value depends on the two
        models being complementary.
      - `samples`: same-model multi-sample count for the list union (`None` -> env `RAG_INGEST_CLAUSE_SAMPLES`,
        default 1).
      - `graph_extract_model`: the model for BOTH party AND affiliation extraction (the GP-1B graph-extract
        surface -- they share one model). A bare model-id string or an `ExtractionModel` (unwrapped to its id);
        `None` -> the default (granite, `RAG_GRAPH_EXTRACT_MODEL`).
      - `judge_model`: the ingest semantic-judge model (ADR-0040 Layer-3 gate) -- a model-id string or an
        `ExtractionModel`; `None` -> `model_for(STRUCTURED_REASONING)`.
      - `chunk_model`: the chunker's boundary-refinement model -- structural boundaries are deterministic (zero
        calls); ONLY an over-cap section triggers a bounded per-section tag-parse call, and this is the model it
        uses. A model-id string or an `ExtractionModel`; `None` -> `model_for(GENERAL)`.
      Env vars remain the fallback for every knob, so existing callers are unaffected."""
    import asyncio
    import hashlib
    import json
    import os
    from pathlib import Path

    from rag_wright.capabilities.disambiguation import disambiguate
    from rag_wright.capabilities.embedding import BGEM3Embedder
    from rag_wright.capabilities.entity_resolution import resolve_entities
    from rag_wright.capabilities.graph_extraction import aproduction_extract_fn
    from rag_wright.capabilities.graph_storage import to_graph
    from rag_wright.capabilities.rlm_chunking import StructuralModelFallbackDiscoverer, achunk
    from rag_wright.contracts.contract_meta import ContractRecord
    from rag_wright.contracts.identifiers import ChunkId
    from rag_wright.contracts.property import ClausePropertyRecord
    from rag_wright.models.profiles import ModelRole, model_for
    from rag_wright.ontology.clause_template import Clause
    from rag_wright.capabilities.dg_extraction import default_extraction_model
    from rag_wright.spans.clause_kg_extractor import classifier_property_extractor
    from rag_wright.spans.dim_classifier import load_dim_registry
    from rag_wright.spans.segment import to_span_record

    parse_dir = Path(cache_dir) / "parsed"
    chunk_dir = Path(cache_dir) / "chunks"
    clause_cache_dir = Path(cache_dir) / "clause_extract"
    party_dir = Path(cache_dir) / "graph_parties"
    affil_dir = Path(cache_dir) / "graph_affiliations"  # issue 0027: separate from the party cache
    for directory in (parse_dir, chunk_dir, clause_cache_dir, party_dir, affil_dir):
        directory.mkdir(parents=True, exist_ok=True)
    if party_seed_path is not None:
        seed_party_cache(party_dir, party_seed_path)
    # ADR-0058 (issue 0004): structure-first default -- deterministic boundaries from docling labels where present
    # (zero model calls), bounded per-section TAG-PARSE fallback for over-cap sections (never the server-side
    # guided-decoding whole-doc call that ran away past the 180s deadline). NOTE: this ingest currently flattens
    # to text (`_parsed_from_text`), so labels are absent here and only the tag-parse fallback fires; preserving
    # docling structure through ingest (a follow-up) unlocks the full zero-model structural win.
    # the chunker's boundary-refinement model: structural boundaries are deterministic (zero calls); only an
    # OVER-CAP section triggers a bounded per-section tag-parse call, and this is the model it uses (issue 0033
    # follow-up). None -> default (GENERAL role). A bare id or an ExtractionModel (unwrapped to its id).
    chunk_model_id = getattr(chunk_model, "model", chunk_model)
    discoverer = StructuralModelFallbackDiscoverer(chunk_model_id)
    summarizer = _NoSummary()
    from rag_wright.spans.semantic_judge import build_asemantic_judge_fn
    # caller-configurable ingest models (else backend/env defaults). A bare id -> an ExtractionModel; for the
    # graph/judge surfaces (which take a model-id string) an ExtractionModel is unwrapped to its `.model` id.
    clause_model = extract_model
    if isinstance(extract_model, str):
        clause_model = default_extraction_model("clause-extract", extract_model)
    graph_extract_id = getattr(graph_extract_model, "model", graph_extract_model)  # None or a bare model-id
    judge_id = getattr(judge_model, "model", judge_model) or model_for(ModelRole.STRUCTURED_REASONING)
    # CLS-D (ADR-0115): Step-3a property extraction is the classifier-first path -- 21 dims via the best-of-both
    # Laya/SetFit fleet, ONE residual LLM call for the 7 numeric/open dims (`clause_model`). No toggle, no full-LLM
    # tag-parse fallback for these dims. The ADR-0028/0040/Layer-3 gates apply unchanged (ClassifierPropertyExtractor).
    clause_extractor = classifier_property_extractor(
        registry=load_dim_registry(),
        model_id=getattr(clause_model, "model", clause_model),  # the residual 7-numeric structured call
        asemantic_judge_fn=build_asemantic_judge_fn(judge_id))
    # party AND affiliation extraction share the graph-extract model (GP-1B); one arg drives both
    aextract_parties_fn = (aproduction_extract_fn(model_id=graph_extract_id) if graph_extract_id
                           else aproduction_extract_fn())
    if classify_fn is None:
        # T55/SETFIT-SEG-1: the clause-function classifier is a SOFT tag (ADR-0047), so its implementation swaps
        # behind this seam with NO contract/API change. DEFAULT is now the in-process trained SetFit ensemble
        # soft-tagger (ms/span, no LLM call -- the ingestion-latency lever). RAG_FUNCTION_CLASSIFIER=llm reverts to
        # the LLM tag-classifier (issue 0005; RAG_MODEL_FUNCTION_CLASSIFY still moves just that model). A passed-in
        # `classify_fn` overrides everything.
        _clf_kind = os.getenv("RAG_FUNCTION_CLASSIFIER", "setfit").lower()
        if _clf_kind == "setfit":
            from rag_wright.spans.clause_function_classifier import production_setfit_clause_classifier

            classify_fn = production_setfit_clause_classifier()
        else:
            from rag_wright.spans.clause_function_classifier import production_batch_clause_classifier

            classify_fn = production_batch_clause_classifier(model_for(ModelRole.FUNCTION_CLASSIFY))
    embedder = embedder if embedder is not None else BGEM3Embedder()
    template_version = hashlib.sha256(
        json.dumps(Clause.model_json_schema(), sort_keys=True).encode("utf-8")).hexdigest()[:12]
    clause_concurrency = int(os.environ.get("CLAUSE_CONCURRENCY", "8"))
    _CLAUSE_EXTRACT_ATTEMPTS = 3

    async def _aparty_names(text: str) -> list:
        parties = await aextract_parties_fn(text)
        return [p.name for p in parties.parties] if parties is not None else []

    # issue 0027: corporate-affiliation extraction (AFFILIATE_OF). Default ON -- the lexical pre-filter keeps it a
    # no-op for contracts that state no affiliation; RAG_INGEST_AFFILIATIONS=0 disables it entirely.
    _affiliations_on = os.getenv("RAG_INGEST_AFFILIATIONS", "1") != "0"

    async def _aaffiliations(text: str) -> list:
        from rag_wright.capabilities.graph_extraction import aextract_affiliations

        if graph_extract_id:  # same graph-extract model as party extraction (issue 0033 follow-up)
            return await aextract_affiliations(text, model_id=graph_extract_id)
        return await aextract_affiliations(text)

    async def chunk_fn(doc: SourceDocument) -> list:
        parsed = _parsed_for(doc, parse_dir)  # CHUNK-7: real docling parse if provided, else a text-only parse
        manifest = await achunk(parsed, summarizer=summarizer, cache_dir=chunk_dir, discoverer=discoverer)
        return list(manifest.chunks)

    async def segment_fn(doc: SourceDocument, chunks: list) -> list:
        segments = await _asegment_and_classify(chunks, classify_fn)
        # issue 0032 (CU-B5): attach source-page provenance to each span. Build a page<->char-offset map over the
        # canonical text (from the parsed doc's per-item prov pages) and look up each span's [doc_start, doc_end).
        # Deterministic, no model call; degrades to no pages when the parse carried no provenance (text-only leg).
        return _attach_page_provenance(doc, chunks, segments, parse_dir)

    async def clauses_fn(doc: SourceDocument, segments: list) -> dict:
        # issue 0038: a Clause is a PROVISION -- clause_extraction_jobs groups spans into provisions (numbered
        # section, else chunk) and yields one job per provision (merged text + anchor span). Retrieval stays per
        # span (index_fn unchanged). The function is a soft tag (ADR-0082), never a gate (issue 0036).
        jobs = clause_extraction_jobs(segments)
        if not jobs:
            return {"clause_records": [], "clause_failures": []}
        failures: list[dict] = []
        sem = asyncio.Semaphore(clause_concurrency)

        from rag_wright.contracts.function import NO_FUNCTION as _NO_FUNCTION

        async def _extract(job: Any) -> Any:
            index, anchor_op, function, scores, text = job  # text = merged provision; anchor_op = citation anchor
            # CLS-D soft-scoping: the anchor span's TOP-3 real function soft-tags scope the classifier lane (union
            # of their dims) -- tolerant of the ~0.5 function accuracy, and kills the over-emission a classifier
            # (which cannot abstain) causes when run unscoped. An untagged span -> () -> no scoping (every dim runs).
            functions = tuple(dict.fromkeys(
                s.function for s in (scores or []) if s.function and s.function != _NO_FUNCTION))[:3]
            clause_cid = ChunkId.of(doc.source_doc_id, index, text)
            cache_file = clause_cache_dir / (hashlib.sha256(
                f"{clause_cid.value}|{function}|{template_version}".encode("utf-8")).hexdigest()[:32] + ".json")
            if cache_file.exists():  # a prior SUCCESSFUL extraction -> reuse it, no granite re-call
                record = ClausePropertyRecord.model_validate_json(cache_file.read_text(encoding="utf-8"))
            else:
                record, reason = await _aextract_clause_with_retry(
                    clause_extractor, chunk_id=clause_cid, function=function, text=text,
                    span_id=anchor_op.span_id, attempts=_CLAUSE_EXTRACT_ATTEMPTS, functions=functions)
                if record is None:  # persistent failure -> record it (PARTIAL), do NOT cache, do NOT silently drop
                    failures.append({"span_id": anchor_op.span_id, "function": function, "reason": reason[:200]})
                    return None
                cache_file.write_text(record.model_dump_json(), encoding="utf-8")
            return record.model_copy(update={"functions": scores})

        async def _bounded(job: Any) -> Any:
            async with sem:  # backpressure (network-bound granite)
                return await _extract(job)

        results = [r for r in await asyncio.gather(*(_bounded(j) for j in jobs)) if r is not None]
        return {"clause_records": results, "clause_failures": failures}

    async def index_fn(doc: SourceDocument, segments: list) -> dict:
        if not segments:
            return {"span_count": 0, "span_failures": []}
        dense_vecs, sparse_vecs = await asyncio.to_thread(
            embedder.encode_batch, [op.text.strip() for op, _, _, _ in segments])

        def _write_all() -> dict:
            count = 0
            failures: list[dict] = []
            for (op, function, chunk_doc_start, scores), dense, sparse in zip(segments, dense_vecs, sparse_vecs):
                try:
                    store.upsert_span(to_span_record(
                        op, contract_id=doc.source_doc_id, chunk_doc_start=chunk_doc_start,
                        dense_vector=list(dense), sparse_vector=sparse, function=function,
                        functions=[s.function for s in scores]))  # T55: top-k soft tags (primary-first)
                    count += 1
                except Exception as exc:  # noqa: BLE001 - a per-span write must not sink the KG, but is NOT swallowed
                    failures.append({"span_id": op.span_id, "reason": repr(exc)})  # 0006-C: surfaced -> PARTIAL
            return {"span_count": count, "span_failures": failures}

        return await asyncio.to_thread(_write_all)

    async def graph_fn(doc: SourceDocument, chunks: list) -> list:  # noqa: ARG001 - GP-1B is per-CONTRACT
        return await aper_contract_graph_extraction(
            doc, party_dir=party_dir, anames_fn=_aparty_names,
            affil_dir=(affil_dir if _affiliations_on else None),
            aaffiliations_fn=(_aaffiliations if _affiliations_on else None))

    async def resolve_fn(extraction_results: list) -> Any:
        return await asyncio.to_thread(
            lambda: to_graph(resolve_entities(
                disambiguate(extraction_results), extraction_results, registry=registry)))

    async def write_fn(doc: SourceDocument, clause_records: list, resolution: Any) -> dict:
        def _write() -> dict:
            for record in clause_records:
                store.write_clause_kg(record)
            nodes, edges = resolution
            store.write_graph(nodes, edges)
            store.upsert_contract(ContractRecord(
                contract_id=doc.source_doc_id, name=doc.metadata.get("raw_title", ""),
                source_doc_id=doc.source_doc_id,
                content_hash=hashlib.sha256(doc.text.encode("utf-8")).hexdigest()))
            return {"clauses": len(clause_records), "entities": len(nodes), "edges": len(edges)}

        return await asyncio.to_thread(_write)

    return abuild_document_ingest(
        chunk_fn, segment_fn, clauses_fn, index_fn, graph_fn, resolve_fn, write_fn)


# (issue 0028 / ADR-0091: `corpus_party_link_fn` -- the KG-7 PartyTo link provider -- was retired with the
#  PartyTo edge. `arun_corpus_ingestion(link_fn=...)` keeps its no-op default; there is no PartyTo provider.)


def register_contract_ingestion_pipeline(registry) -> None:
    """LG-3d: register `contract_ingestion_pipeline` (composite subgraph; source docs -> populated contract KG)."""
    registry.register(
        "contract_ingestion_pipeline",
        contract=IngestionReport,
        kind="subgraph",
        display_name="Contract ingestion pipeline (corpus -> populated, connected KG)",
    )
