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


class IngestionReport(BaseModel):
    """The composite's output: how many documents were ingested, which dead-lettered (with reasons), and how
    many `PARTY_TO` links the final connect step wrote."""

    documents_ingested: int
    dead_lettered: list[dict]
    party_links: int
    per_document: list[dict]
    # PROD-3 lossless invariant (ADR-0050): documents written but INCOMPLETE (>=1 clause extraction failed after
    # retries). Surfaced here so a partial is KNOWN at job completion, never discovered later by grepping logs.
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
LinkFn = Callable[[], int]  # corpus-level: party_clause_linking -> #PARTY_TO edges


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


def _print_progress(message: str) -> None:
    print(message, flush=True)




async def arun_corpus_ingestion(
    adapter: CorpusAdapter, ingest_graph: Any, *, link_fn: LinkFn = lambda: 0,
    progress: Callable[[str], None] = _print_progress,
    is_done: Callable[[SourceDocument], bool] = lambda _doc: False,
) -> IngestionReport:
    """ASYNC-B2e (ADR-0057): the async twin of `run_corpus_ingestion`. Maps each document through the ASYNC
    per-document `ingest_graph` via `ainvoke` -- so the model calls carry the true wall-clock deadline and the
    per-document parallel branches run concurrently on the loop. Same X/N progress, resume-skip, dead-letter, and
    partial semantics as the sync driver (documents are processed sequentially; intra-document parallelism comes
    from the graph)."""
    documents = list(adapter.documents())
    total = len(documents)
    progress(f"[ingest] starting: {total} documents")

    ingested = skipped = 0
    dead_lettered: list[dict] = []
    partial: list[dict] = []
    per_document: list[dict] = []
    for i, document in enumerate(documents, 1):
        if is_done(document):
            ingested += 1
            skipped += 1
            if skipped % 25 == 0 or i == total:
                progress(f"[ingest] {i}/{total} resume-skipping already-done docs ({skipped} skipped so far)")
            continue
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
        if clause_failures or span_failures:  # 0006-C: EITHER kind of loss flags the doc PARTIAL (never silent)
            entry = {"source_doc_id": document.source_doc_id}
            if clause_failures:
                entry["clause_failures"] = clause_failures
            if span_failures:
                entry["span_failures"] = span_failures
            partial.append(entry)
            reasons = ", ".join(
                p for p in (f"{len(clause_failures)} clause(s)" if clause_failures else "",
                            f"{len(span_failures)} span(s)" if span_failures else "") if p)
            progress(f"[ingest] {i}/{total} {document.source_doc_id} PARTIAL ({reasons} failed) {summary}")
        else:
            progress(f"[ingest] {i}/{total} {document.source_doc_id} OK {summary}")

    progress(f"[ingest] {ingested}/{total} present ({skipped} resume-skipped), {len(dead_lettered)} "
             f"dead-lettered, {len(partial)} partial; linking parties (KG-7)...")
    party_links = link_fn()
    progress(f"[ingest] done: {ingested}/{total} ingested ({skipped} resume-skipped), "
             f"{len(dead_lettered)} dead-lettered, {len(partial)} partial, {party_links} PARTY_TO edges")
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


def parsed_source_document(
    source_doc_id: str, name: str, data: bytes, *, cache_dir: Any, metadata: Optional[dict] = None
) -> SourceDocument:
    """Build a STRUCTURE-BEARING `SourceDocument` from raw document BYTES (PDF/DOCX/HTML/MD): docling-parse ONCE
    (content-hash gated + cached), carry the `DoclingDocument` on `.parsed` (so the chunker's structural pass
    fires on real headings), and set `.text` to the flattened text (for the text-consuming stages). This is how a
    byte-source corpus adapter -- or the product (RuleWright), which hand-builds its ingest -- feeds a real
    document to the engine; a plain-text `SourceDocument` (no `.parsed`) still uses the text fallback."""
    import hashlib
    from pathlib import Path

    from rag_wright.capabilities.parsing import load_document
    from rag_wright.corpus.document_parser import document_to_text, parse_document_bytes

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    content_hash = hashlib.sha256(data).hexdigest()
    manifest_path = cache_dir / f"{source_doc_id}.{content_hash[:16]}.json"
    parsed = ParsedDocument(
        source_doc_id=source_doc_id, content_hash=content_hash, manifest_path=str(manifest_path))
    if manifest_path.exists():  # content-hash gate: parse once
        document = load_document(parsed)
    else:
        document = parse_document_bytes(name, data)
        document.save_as_json(manifest_path)
    return SourceDocument(
        source_doc_id=source_doc_id, text=document_to_text(document), parsed=parsed, metadata=metadata or {})


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
    doc: SourceDocument, *, party_dir: Any, anames_fn: Callable[[str], Any]) -> list:
    """ASYNC-B2c (ADR-0057): the async twin of `per_contract_graph_extraction`. The only model call (party
    names) runs on the async seam via `anames_fn` (true wall-clock deadline); the cache and
    `parties_to_extraction` are sync. Same cache semantics and result."""
    cache_file = _party_cache_file(party_dir, doc)
    names = _cached_party_names(cache_file)
    if names is None:
        names = await anames_fn(doc.text)
        _write_party_cache(cache_file, names)
    return _parties_extraction(names, doc)


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




async def _asegment_and_classify(chunks: list, classify_fn: Any, *, segment: Any = None) -> list:
    """ASYNC-B2e (ADR-0057): the async twin of `_segment_and_classify` -- classify each chunk's spans via the
    async classifier (`aclassify_spans`, true wall-clock deadline). Segmentation (`segment_clause`) is CPU/regex,
    kept sync."""
    from rag_wright.contracts.function import NO_FUNCTION, primary_function

    seg = segment
    if seg is None:
        from rag_wright.spans.segment import segment_clause

        seg = segment_clause
    out: list = []
    for ch in chunks:
        ops = [op for op in seg(ch.chunk_id, ch.text) if op.text.strip()]
        if not ops:
            continue
        scores_per_span = await classify_fn.aclassify_spans(ch.text, [op.text for op in ops])
        for op, scores in zip(ops, scores_per_span):
            out.append((op, primary_function(scores) or NO_FUNCTION, ch.doc_start, scores))
    return out




def aproduction_document_ingest(
    store: Any, *, cache_dir: Any, registry: Any, embedder: Any = None, party_seed_path: Any = None,
    classify_fn: Any = None):
    """ASYNC-B2e (ADR-0057): the async twin of `production_document_ingest`. Wires the ASYNC stage seams (achunk,
    aclassify_spans, clause_extractor.aextract, aper_contract_graph_extraction) so the ingest model calls run on
    the async seam with the true wall-clock deadline; CPU/store work (embed, resolve, DB writes) runs off the loop
    via `asyncio.to_thread`. Clause extraction is bounded-concurrent via `asyncio.gather` + a `Semaphore`. Returns
    an ASYNC per-document graph -- drive it with `arun_corpus_ingestion`."""
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
    from rag_wright.contracts.function import canonical_function
    from rag_wright.contracts.identifiers import ChunkId
    from rag_wright.contracts.property import ClausePropertyRecord
    from rag_wright.models.profiles import ModelRole, model_for
    from rag_wright.ontology.clause_template import Clause
    from rag_wright.spans.clause_kg_extractor import granite_clause_extractor
    from rag_wright.spans.segment import to_span_record

    parse_dir = Path(cache_dir) / "parsed"
    chunk_dir = Path(cache_dir) / "chunks"
    clause_cache_dir = Path(cache_dir) / "clause_extract"
    party_dir = Path(cache_dir) / "graph_parties"
    for directory in (parse_dir, chunk_dir, clause_cache_dir, party_dir):
        directory.mkdir(parents=True, exist_ok=True)
    if party_seed_path is not None:
        seed_party_cache(party_dir, party_seed_path)
    # ADR-0058 (issue 0004): structure-first default -- deterministic boundaries from docling labels where present
    # (zero model calls), bounded per-section TAG-PARSE fallback for over-cap sections (never the server-side
    # guided-decoding whole-doc call that ran away past the 180s deadline). NOTE: this ingest currently flattens
    # to text (`_parsed_from_text`), so labels are absent here and only the tag-parse fallback fires; preserving
    # docling structure through ingest (a follow-up) unlocks the full zero-model structural win.
    discoverer = StructuralModelFallbackDiscoverer()
    summarizer = _NoSummary()
    from rag_wright.spans.semantic_judge import build_asemantic_judge_fn
    clause_extractor = granite_clause_extractor(
        asemantic_judge_fn=build_asemantic_judge_fn(model_for(ModelRole.STRUCTURED_REASONING)))
    aextract_parties_fn = aproduction_extract_fn()
    if classify_fn is None:
        from rag_wright.spans.clause_function_classifier import production_batch_clause_classifier

        # issue 0005: the classifier has its OWN role (FUNCTION_CLASSIFY) -- defaults to the product LLM (granite,
        # unchanged), but RAG_MODEL_FUNCTION_CLASSIFY moves JUST this stage (e.g. to Gemma-4) without touching the
        # others. Route (b) tag-parse runs on any model. A `classify_fn` passed in still overrides everything.
        classify_fn = production_batch_clause_classifier(model_for(ModelRole.FUNCTION_CLASSIFY))
    embedder = embedder if embedder is not None else BGEM3Embedder()
    template_version = hashlib.sha256(
        json.dumps(Clause.model_json_schema(), sort_keys=True).encode("utf-8")).hexdigest()[:12]
    clause_concurrency = int(os.environ.get("CLAUSE_CONCURRENCY", "8"))
    _CLAUSE_EXTRACT_ATTEMPTS = 3

    async def _aparty_names(text: str) -> list:
        parties = await aextract_parties_fn(text)
        return [p.name for p in parties.parties] if parties is not None else []

    async def chunk_fn(doc: SourceDocument) -> list:
        parsed = _parsed_for(doc, parse_dir)  # CHUNK-7: real docling parse if provided, else a text-only parse
        manifest = await achunk(parsed, summarizer=summarizer, cache_dir=chunk_dir, discoverer=discoverer)
        return list(manifest.chunks)

    async def segment_fn(doc: SourceDocument, chunks: list) -> list:  # noqa: ARG001 - segments are per-chunk
        return await _asegment_and_classify(chunks, classify_fn)

    async def clauses_fn(doc: SourceDocument, segments: list) -> dict:
        jobs = [
            (index, op, canonical_function(function), scores)
            for index, (op, function, _cds, scores) in enumerate(segments)
            if canonical_function(function) is not None
        ]
        if not jobs:
            return {"clause_records": [], "clause_failures": []}
        failures: list[dict] = []
        sem = asyncio.Semaphore(clause_concurrency)

        async def _extract(job: Any) -> Any:
            index, op, function, scores = job
            clause_cid = ChunkId.of(doc.source_doc_id, index, op.text)
            cache_file = clause_cache_dir / (hashlib.sha256(
                f"{clause_cid.value}|{function}|{template_version}".encode("utf-8")).hexdigest()[:32] + ".json")
            if cache_file.exists():  # a prior SUCCESSFUL extraction -> reuse it, no granite re-call
                record = ClausePropertyRecord.model_validate_json(cache_file.read_text(encoding="utf-8"))
            else:
                record = None
                reason = ""
                for _attempt in range(_CLAUSE_EXTRACT_ATTEMPTS):  # retry the transient (rare LLM-JSON garble)
                    try:
                        record = await clause_extractor.aextract(
                            chunk_id=clause_cid, function=function, text=op.text, span_id=op.span_id)
                        break
                    except Exception as exc:  # noqa: BLE001 - retry; a PERSISTENT failure is recorded below
                        reason = str(exc)
                if record is None:  # persistent failure -> record it (PARTIAL), do NOT cache, do NOT silently drop
                    failures.append({"span_id": op.span_id, "function": function, "reason": reason[:200]})
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
            for (op, function, chunk_doc_start, _scores), dense, sparse in zip(segments, dense_vecs, sparse_vecs):
                try:
                    store.upsert_span(to_span_record(
                        op, contract_id=doc.source_doc_id, chunk_doc_start=chunk_doc_start,
                        dense_vector=list(dense), sparse_vector=sparse, function=function))
                    count += 1
                except Exception as exc:  # noqa: BLE001 - a per-span write must not sink the KG, but is NOT swallowed
                    failures.append({"span_id": op.span_id, "reason": repr(exc)})  # 0006-C: surfaced -> PARTIAL
            return {"span_count": count, "span_failures": failures}

        return await asyncio.to_thread(_write_all)

    async def graph_fn(doc: SourceDocument, chunks: list) -> list:  # noqa: ARG001 - GP-1B is per-CONTRACT
        return await aper_contract_graph_extraction(doc, party_dir=party_dir, anames_fn=_aparty_names)

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


def corpus_party_link_fn(store: Any, mentions_path: Any) -> LinkFn:
    """Build the corpus-level PARTY_TO link step (KG-7, run ONCE after ingestion) -- CORPUS-GENERIC. Defaults to
    the TRUE many-to-many derivation (PARTY-TO-MANY-TO-MANY, ADR-0036): loads a per-contract party-mention cache
    (`{source_doc_id: [party names]}`) so a party links to EVERY contract it signed -- the same path as
    `scripts/link_party_clause.py MANY=1`. Falls back to the single-provenance KG-7 join only when the cache is
    absent. Loaded lazily (at link time) so it reflects the cache on disk when the corpus finishes. A corpus
    driver passes its own `mentions_path`; the flow is corpus-agnostic."""
    import json
    from pathlib import Path

    from rag_wright.capabilities.party_clause_linking import party_clause_linking

    def _link() -> int:
        path = Path(mentions_path)
        mentions = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        return len(party_clause_linking(store, mentions=mentions).links)

    return _link


def register_contract_ingestion_pipeline(registry) -> None:
    """LG-3d: register `contract_ingestion_pipeline` (composite subgraph; source docs -> populated contract KG)."""
    registry.register(
        "contract_ingestion_pipeline",
        contract=IngestionReport,
        kind="subgraph",
        display_name="Contract ingestion pipeline (corpus -> populated, connected KG)",
    )
