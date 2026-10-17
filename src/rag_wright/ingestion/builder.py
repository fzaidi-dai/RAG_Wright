"""ING-4b (ADR-0124): `build_ingestion` -- the engine's generic ingestion pipeline around a domain's hooks.

The engine owns the mechanism: parse (hidden sheets, embedded children), chunk, layout, segment, tag, index the spans
(embed + store), group into units, extract records concurrently, write, record a `Document` node per document, and
ingest embedded children through the same pipeline with `EmbeddedIn` / `AttachedTo` edges (ING-6 link confidence).
Every hook's output is held to its contract (`check_tiling` / `check_units` / `check_extraction`); a unit whose
extraction fails is recorded and skipped, a document that fails is dead-lettered, and the run goes on. Progress is
streamed as `[ingest] i/N ...` lines.

A domain passes only its `extractor`; every other hook has an engine default (ING-2/3/4a). Thresholds come from
`IngestionTuning` (the builder's, else `EngineConfig.options.ingest.tuning`, else the defaults).
"""
from __future__ import annotations

import asyncio
import functools
import hashlib
import mimetypes
import operator
import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Awaitable, Callable, Optional, Sequence, TypedDict, Union

from rag_wright.contracts.ingestion import (
    BoundaryDecider,
    Extractor,
    IngestionTuning,
    IngestSource,
    RecordWriter,
    Segmenter,
    Span,
    SpanTagger,
    TaggedSpan,
    Unit,
    UnitExtraction,
    UnitGrouper,
    UnitRepresentative,
    check_extraction,
    check_tiling,
    check_units,
)
from rag_wright.store.seam import KgEdge, KgNode

_DOC_ID = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_CHILD_DEPTH = 2  # an embedded file inside an embedded file is ingested; deeper nesting is reported, not walked


@dataclass
class DocumentReport:
    """What ingesting one document did. Counts: `chunks`, `spans` (indexed), `units` (extracted), `records` (nodes
    written). `extraction_failures` (`{unit, anchor, reason}`) / `span_failures` (`{span_id, reason}`): the units /
    spans whose extraction or indexing failed; the rest of the document still lands. `skipped_hidden_sheets`: hidden spreadsheet
    sheets left out (`IngestSource.include_hidden_sheets=False`). `children`: the ids of embedded files and PDF
    attachments ingested as child documents (`parent_doc_id` is set on theirs); `embedded_skipped`: embedded files
    that could not be ingested. `links`: `AttachedTo` record links written, by confidence; `unmapped_links`: links
    whose record row could not be found. `dead_letter`: why the whole document failed (None when it landed)."""

    doc_id: str
    parent_doc_id: Optional[str] = None
    chunks: int = 0
    spans: int = 0
    units: int = 0
    records: int = 0
    extraction_failures: list[dict] = field(default_factory=list)
    span_failures: list[dict] = field(default_factory=list)
    skipped_hidden_sheets: list[str] = field(default_factory=list)
    embedded_skipped: list[str] = field(default_factory=list)
    children: list[str] = field(default_factory=list)
    links: dict[str, int] = field(default_factory=dict)  # AttachedTo edges written, by confidence
    unmapped_links: int = 0  # record links whose row span could not be found
    dead_letter: Optional[str] = None


@dataclass
class IngestionReport:
    """The result of `IngestionPipeline.aingest`: one `DocumentReport` per document (embedded children included).
    `failed` counts the dead-lettered documents and `succeeded` the rest (properties)."""

    documents: list[DocumentReport] = field(default_factory=list)

    @property
    def failed(self) -> int:
        return sum(d.dead_letter is not None for d in self.documents)

    @property
    def succeeded(self) -> int:
        return len(self.documents) - self.failed

    @classmethod
    def from_dict(cls, value: dict) -> "IngestionReport":
        """The report from its plain form (`dataclasses.asdict`), as the ingestion graph's output carries it."""
        return cls(documents=[DocumentReport(**d) for d in value["documents"]])


class _NoSummary:
    def summarize(self, text: str) -> str:  # noqa: ARG002 - chunk summaries are not part of the generic path
        return ""


def _doc_id(path: str) -> str:
    """A document id from the file name INCLUDING its type, so a PDF export and its spreadsheet original (same name)
    never collide: `Report v3.xlsm` -> `Report_v3_xlsm`."""
    p = Path(path)
    stem = _DOC_ID.sub("_", p.stem).strip("_")[:80] or "document"
    return f"{stem}_{p.suffix.lstrip('.').lower()}" if p.suffix else stem


# (ws, source_document, chunks); awaited once per document after its records are written; its return value is ignored
DocumentHook = Callable[[Any, Any, list], Awaitable[Any]]


@dataclass
class ExtractStage:
    """What the group-and-extract stage produced for one document."""

    units: list = field(default_factory=list)
    extractions: list = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)
    row_spans: dict = field(default_factory=dict)


class IngestionStages:
    """ING-4c: the engine's per-document ingestion STAGES -- one implementation, driven by `IngestionPipeline.aingest`
    and by any other driver (the reference contract pipeline runs them as its LangGraph nodes). Each stage enforces
    its hook contract; a stage failure raises (the driver decides: dead-letter, retry)."""

    def __init__(self, store: Any, *, extractor: Extractor, segmenter: Optional[Segmenter] = None,
                 span_tagger: Optional[SpanTagger] = None, unit_grouper: Optional[UnitGrouper] = None,
                 boundary_decider: Optional[BoundaryDecider] = None, writer: Optional[RecordWriter] = None,
                 tuning: Optional[IngestionTuning] = None, embedder: Any = None, chunk_model: Optional[str] = None,
                 cache_dir: Union[str, Path], discoverer: Any = None,
                 unit_representative: Optional[UnitRepresentative] = None) -> None:
        self.store = store
        self.discoverer = discoverer  # a chunk-boundary discoverer; default: structural + `chunk_model` refinement
        self.extractor = extractor
        self.segmenter = segmenter
        self.tagger = span_tagger
        self.grouper = unit_grouper
        self.representative = unit_representative
        self.decider = boundary_decider
        self.writer = writer
        self.tuning = tuning or IngestionTuning()
        self.embedder = embedder
        self.chunk_model = chunk_model
        self.cache = Path(cache_dir)
        self._parsed: dict[str, Any] = {}

    def parsed(self, sd: Any) -> Any:
        """The document's parse: its own (`parse_document`), else a one-item-per-line text parse (text-only input)."""
        from rag_wright.capabilities.document_parse import parsed_text_document

        if sd.source_doc_id not in self._parsed:
            self._parsed[sd.source_doc_id] = sd.parsed if sd.parsed is not None else parsed_text_document(
                sd.source_doc_id, sd.text, self.cache / "parsed")
        return self._parsed[sd.source_doc_id]

    def document(self, sd: Any) -> Any:
        from rag_wright.capabilities.parsing import load_document

        return load_document(self.parsed(sd))

    async def chunk(self, sd: Any) -> list:
        """Chunks; an EMPTY list for a document with no text (a blank page, a sheet holding only attachments) -- it is
        still recorded with its children, never dead-lettered for having nothing to chunk."""
        from rag_wright.capabilities.rlm_chunking import StructuralModelFallbackDiscoverer, achunk
        from rag_wright.corpus.document_parser import content_items

        if not any((it.text or "").strip() for it in content_items(self.document(sd))):
            return []
        manifest = await achunk(self.parsed(sd), summarizer=_NoSummary(), cache_dir=self.cache / "chunks",
                                discoverer=self.discoverer or StructuralModelFallbackDiscoverer(self.chunk_model))
        return list(manifest.chunks)

    async def segment(self, sd: Any, chunks: list) -> list[TaggedSpan]:
        """Spans per chunk (tiling checked), whitespace-only spans dropped (nothing to retrieve or extract), page
        provenance attached, then tagged."""
        from rag_wright.ingestion.layout import chunk_layouts
        from rag_wright.ingestion.segment import segment_layout

        if not chunks:
            return []
        document = self.document(sd)
        layouts = chunk_layouts(document, [c.text for c in chunks])
        segment = self.segmenter or functools.partial(segment_layout, tuning=self.tuning)
        per_chunk: list[tuple[Any, list[Span]]] = []
        for chunk, layout in zip(chunks, layouts):
            spans = list(segment(chunk.chunk_id, chunk.text, layout))
            check_tiling(chunk.chunk_id, chunk.text, spans)
            spans = [s for s in spans if s.text.strip()]
            per_chunk.append((chunk, _with_pages(document, chunks, chunk, spans)))
        if self.tagger is not None:
            tagged_lists = await asyncio.gather(*(self.tagger(c.text, ss) for c, ss in per_chunk))
        else:
            tagged_lists = [[TaggedSpan(span=s) for s in ss] for _c, ss in per_chunk]
        return [t for ts in tagged_lists for t in ts]

    async def index(self, sd: Any, tagged: list[TaggedSpan], chunks: list) -> dict:
        """Embed + store each span. Returns `{"span_count", "span_failures"}` (a failed span write is reported)."""
        from rag_wright.contracts.span import to_span_record

        out: dict = {"span_count": 0, "span_failures": []}
        if not tagged:
            return out
        doc_start = {c.chunk_id: (c.doc_start or 0) for c in chunks}
        dense, sparse = await asyncio.to_thread(self.embedder.encode_batch, [t.span.text.strip() for t in tagged])

        def write() -> None:
            for t, d, sp in zip(tagged, dense, sparse):
                try:
                    self.store.upsert_span(to_span_record(
                        t.span, document_id=sd.source_doc_id, chunk_doc_start=doc_start[t.span.parent_chunk_id],
                        dense_vector=list(d), sparse_vector=sp, primary_tag=t.primary_tag, tags=list(t.tags)))
                    out["span_count"] += 1
                except Exception as exc:  # noqa: BLE001 - a failed span write is reported, not swallowed
                    out["span_failures"].append({"span_id": t.span.span_id, "reason": repr(exc)})

        await asyncio.to_thread(write)
        return out

    async def extract(self, sd: Any, tagged: list[TaggedSpan], *, table_mode: str = "auto") -> ExtractStage:
        """Group into units (checked), attach table rows, extract each unit concurrently (provenance checked); a failed
        unit is recorded and skipped."""
        from rag_wright.ingestion.group import apply_unit_representative, group_units

        stage = ExtractStage()
        if not tagged:
            return stage
        grouper = self.grouper or functools.partial(group_units, tuning=self.tuning, table_mode=table_mode)
        stage.units = await grouper(tagged, decider=self.decider)
        if self.representative is not None:  # PS-R3: the domain's choice of each unit's representative span
            stage.units = apply_unit_representative(stage.units, tagged, self.representative)
        check_units([t.span for t in tagged], stage.units)
        document = self.document(sd)
        stage.row_spans = _row_spans(document, [t.span for t in tagged])
        _attach_table_rows(document, stage.units, stage.row_spans)
        sem = asyncio.Semaphore(self.tuning.extract_concurrency)

        async def one(unit: Any) -> Optional[UnitExtraction]:
            async with sem:
                try:
                    extraction = await self.extractor(unit, source_doc_id=sd.source_doc_id)
                    check_extraction(unit, extraction)
                    return extraction
                except Exception as exc:  # noqa: BLE001 - a failed unit is recorded and skipped
                    stage.failures.append({"unit": unit.index, "anchor": unit.anchor.span_id,
                                           "reason": f"{type(exc).__name__}: {exc}"[:300]})
                    return None

        stage.extractions = [e for e in await asyncio.gather(*(one(u) for u in stage.units)) if e is not None]
        return stage

    async def write(self, sd: Any, extractions: list) -> int:
        """Persist the records (the domain's writer, else `kg_write`). Returns the number of record nodes."""
        if self.writer is not None:
            await self.writer(sd.source_doc_id, extractions)
        else:
            nodes = [n for e in extractions for n in e.nodes]
            edges = [ed for e in extractions for ed in e.edges]
            await asyncio.to_thread(self.store.kg_write, nodes, edges)
        return sum(len(e.nodes) for e in extractions)

    def write_document_node(self, doc_id: str, *, parent_id: Optional[str], filename: str, media_type: str,
                            sha256: str, metadata: Optional[dict] = None) -> None:
        """The `Document` node: the engine's fields plus the product's `metadata` (PS-13), in one write."""
        self.store.kg_write([KgNode("Document", "doc_id", {
            **(metadata or {}), "doc_id": doc_id, "parent_doc_id": parent_id or "", "filename": filename,
            "media_type": media_type, "sha256": sha256})])

    def write_child_links(self, parent_id: str, child: Any, row_spans: dict, rep: DocumentReport) -> None:
        """The child's `EmbeddedIn` edge (its first anchor's position) and an `AttachedTo` edge per record link."""
        first = child.anchors[0] if child.anchors else None
        position = {k: v for k, v in (first.model_dump() if first else {}).items()
                    if v is not None and k not in ("table_ref", "table_row")}
        edges = [KgEdge("EmbeddedIn", "Document", "doc_id", child.doc_id, "Document", "doc_id", parent_id,
                        {**position, "anchors": len(child.anchors)})]
        for link in child.links:
            span_id = row_spans.get((link.table_ref, link.table_row))
            if span_id is None:
                rep.unmapped_links += 1
                continue
            edges.append(KgEdge("AttachedTo", "Document", "doc_id", child.doc_id, "Span", "span_id", span_id,
                                {"confidence": link.confidence.value, "basis": link.basis,
                                 "evidence": " ".join(link.evidence)}))
            rep.links[link.confidence.value] = rep.links.get(link.confidence.value, 0) + 1
        self.store.kg_ensure_edges(edges)


class IngestionPipeline:
    """Built by `build_ingestion`; run with `await pipeline.aingest(ws, sources, cache_dir=...)`. Drives the shared
    `IngestionStages` per document (parse -> chunk -> segment -> tag -> index -> group -> extract -> write ->
    `document_hook` -> `Document` node), then ingests embedded files and PDF attachments the same way, as child
    documents linked to their parent."""

    def __init__(self, extractor: Extractor, *, segmenter: Optional[Segmenter], span_tagger: Optional[SpanTagger],
                 unit_grouper: Optional[UnitGrouper], boundary_decider: Optional[BoundaryDecider],
                 writer: Optional[RecordWriter], tuning: Optional[IngestionTuning], embedder: Any,
                 chunk_model: Optional[str], progress: Callable[[str], Any],
                 document_hook: Optional[DocumentHook] = None,
                 unit_representative: Optional[UnitRepresentative] = None,
                 chunk_discoverer: Any = None) -> None:
        self._hooks = dict(extractor=extractor, segmenter=segmenter, span_tagger=span_tagger,
                           unit_grouper=unit_grouper, boundary_decider=boundary_decider, writer=writer,
                           unit_representative=unit_representative)
        self._tuning = tuning
        self._embedder = embedder
        self._chunk_model = chunk_model
        self._progress = progress
        self._document_hook = document_hook
        self._chunk_discoverer = chunk_discoverer  # PS-R5b: the product's chunk-boundary rule (None = engine default)

    def stages(self, ws: Any, *, cache_dir: Union[str, Path]) -> IngestionStages:
        """Advanced: the individual stage functions bound to a workspace (its store, ingest embedder and tuning), for a
        domain that drives the stages itself (the reference contract pipeline does). Most domains only need
        `aingest`."""
        tuning = self._tuning or getattr(ws._config.options.ingest, "tuning", None) or IngestionTuning()
        if self._embedder is None:
            from rag_wright.capabilities.embedding_profiles import build_ingest_embedder

            self._embedder = build_ingest_embedder(ws._config.embeddings.get("text", "bge-m3"))
        return IngestionStages(ws._store, **self._hooks, tuning=tuning, embedder=self._embedder,
                               discoverer=self._chunk_discoverer,
                               chunk_model=self._chunk_model, cache_dir=cache_dir)

    async def aingest(self, ws: Any, sources: Sequence[Union[str, Path, IngestSource]], *,
                      cache_dir: Union[str, Path], checkpointer: Any = None,
                      run_id: Optional[str] = None) -> IngestionReport:
        """Ingest every source (and its embedded children) into the workspace `ws` (from `open_workspace`).
        `sources` are file paths or `IngestSource`s (a path, or in-memory bytes with a file name); `cache_dir` holds the content-hash-gated parse and chunk caches
        (a re-ingest of an unchanged file re-uses them). Documents run concurrently up to
        `tuning.document_concurrency`; a document that fails is dead-lettered in its `DocumentReport`, never raised.
        Returns the `IngestionReport`. Every model role resolves through the workspace's `EngineConfig.models`
        (PS-14).

        LGI-3, resuming: pass a LangGraph `checkpointer` (yours, e.g. a Postgres saver) and a `run_id` naming the run.
        If the process stops mid-run, call `aingest` again with the same sources, `cache_dir`, checkpointer and
        `run_id`: it carries on from the last finished stage of each document, re-running none that finished. A
        `run_id` whose run finished raises `ValueError` (start a new run with a new id), as do different sources."""
        from rag_wright.api.workspace import use_workspace_models

        if (checkpointer is None) != (run_id is None):
            raise ValueError("resuming needs both a checkpointer and a run_id (or neither)")
        with use_workspace_models(ws):
            return await self._aingest(ws, sources, cache_dir=cache_dir, checkpointer=checkpointer, run_id=run_id)

    async def _aingest(self, ws: Any, sources: Sequence[Union[str, Path, IngestSource]], *,
                       cache_dir: Union[str, Path], checkpointer: Any, run_id: Optional[str]) -> IngestionReport:
        items = _sources(sources)
        if checkpointer is None:
            return IngestionReport.from_dict(
                (await self.graph(ws, cache_dir=cache_dir).ainvoke({"sources": items}))["report"])
        config = {"configurable": {"thread_id": run_id}}
        saved = await self.graph(ws, cache_dir=cache_dir, checkpointer=checkpointer).aget_state(config)
        if not saved.values:
            graph = self.graph(ws, cache_dir=cache_dir, checkpointer=checkpointer)
            return IngestionReport.from_dict((await graph.ainvoke({"sources": items}, config))["report"])
        if not saved.next:
            raise ValueError(f"ingest run {run_id!r} already finished; start a new run with a new run_id")
        if saved.values.get("sources") != _dump(list[IngestSource], items):
            raise ValueError(f"ingest run {run_id!r} was started with different sources; resume it with the same "
                             f"sources, or start a new run with a new run_id")
        done = sum(t.name == "document" and t.result is not None for t in saved.tasks)
        self._progress(f"[ingest] resume {run_id}: {done}/{len(items)} documents done")
        graph = self._run_graph(ws, cache_dir=cache_dir, checkpointer=checkpointer, done=done)
        return IngestionReport.from_dict((await graph.ainvoke(None, config))["report"])

    def graph(self, ws: Any, *, cache_dir: Union[str, Path], checkpointer: Any = None) -> Any:
        """LGI-2: the whole run as one compiled LangGraph `StateGraph`, bound to the workspace `ws`:

            start --(one Send per source)--> document --> finish

        `document` runs one source through `document_graph` (embedded files included); at most
        `tuning.document_concurrency` documents are in flight. The input is `{"sources": [...]}` (file paths or
        `IngestSource`s); the output's `report` is the `IngestionReport` as plain data (`IngestionReport.from_dict`
        rebuilds it), its documents in source order. This is what
        `aingest` runs (without `aingest`'s workspace model scoping: wrap the call in `use_workspace_models(ws)` when
        the workspace overrides model roles). Progress goes to the pipeline's `progress` callback. With a
        `checkpointer`, every stage of every document is checkpointed on the thread named by the call's
        `configurable.thread_id` (see `aingest` for resuming). The state holds only built-in values (dicts, lists,
        strings, numbers, bytes, dates), so it round-trips through any saver, including LangGraph's strict
        msgpack mode; a tuple inside a record's free-form `props` comes back from a checkpoint as a list."""
        return self._run_graph(ws, cache_dir=cache_dir, checkpointer=checkpointer, done=0)

    def _run_graph(self, ws: Any, *, cache_dir: Union[str, Path], checkpointer: Any, done: int) -> Any:
        """The run graph; `done` = documents a resumed run already finished (its `i/N` lines continue from there)."""
        from langgraph.graph import END, START, StateGraph
        from langgraph.types import Send

        stages = self.stages(ws, cache_dir=cache_dir)
        document_graph = self.document_graph(ws, stages)
        sem = asyncio.Semaphore(stages.tuning.document_concurrency)
        counter = {"done": done}

        def start(state: _RunState) -> dict:
            items = _dump(list[IngestSource], _sources(state["sources"]))
            self._progress(f"[ingest] start N={len(items)}")
            _check_model_ceilings(stages.tuning, len(items), self._progress)
            return {"sources": items}

        def send(state: _RunState) -> Any:
            total = len(state["sources"])
            return [Send("document", {"source": src, "total": total}) for src in state["sources"]] or "finish"

        async def document(state: _SourceState) -> dict:
            src = _load(IngestSource, state["source"])
            async with sem:
                docs = await self._ingest_file(ws, stages, src.doc_id or _doc_id(src.display_name), src,
                                               parent_id=None, child=None, depth=0, graph=document_graph)
            counter["done"] += 1
            top = docs[0]
            self._progress(f"[ingest] {counter['done']}/{state['total']} {top.doc_id} "
                           + (f"DEAD-LETTER: {top.dead_letter}" if top.dead_letter else
                              f"spans={top.spans} units={top.units} records={top.records} "
                              f"children={len(top.children)} failures={len(top.extraction_failures)}"))
            return {"documents": [_dump(list[DocumentReport], docs)]}

        def finish(state: _RunState) -> dict:
            report = IngestionReport.from_dict({"documents": [d for docs in state.get("documents", []) for d in docs]})
            self._progress(f"[ingest] done {report.succeeded} ok, {report.failed} dead-lettered")
            return {"report": _dump(IngestionReport, report)}

        g = StateGraph(_RunState)
        g.add_node("start", start)
        g.add_node("document", document, input_schema=_SourceState)
        g.add_node("finish", finish)
        g.add_edge(START, "start")
        g.add_conditional_edges("start", send, ["document", "finish"])
        g.add_edge("document", "finish")
        g.add_edge("finish", END)
        return g.compile(checkpointer=checkpointer, name="ingestion_run")

    async def _ingest_file(self, ws: Any, stages: IngestionStages, doc_id: str, src: IngestSource, *,
                           parent_id: Optional[str], child: Optional[Any], depth: int,
                           graph: Any = None) -> list[DocumentReport]:
        """Ingest one document (`child` = its `EmbeddedChild` record when it was extracted from `parent_id`), then
        its own embedded children, by running the per-document graph (`document_graph`)."""
        from rag_wright.capabilities.document_parse import EmbeddedChild

        graph = graph or self.document_graph(ws, stages)
        state = await graph.ainvoke({"doc_id": doc_id, "source": _dump(IngestSource, src), "parent_id": parent_id,
                                     "child": None if child is None else _dump(EmbeddedChild, child), "depth": depth})
        return _load(list[DocumentReport], state["reports"])

    def document_graph(self, ws: Any, stages: IngestionStages) -> Any:
        """LGI-1: the per-document ingestion graph, a compiled LangGraph `StateGraph` over `stages` (bound to `ws`):

            parse -> chunk -> segment -> index -> extract -> write -> document_hook -> document_node -> children

        A stage that raises routes to `dead_letter`, which ends the document with its `DocumentReport.dead_letter`
        set (never raised, so one bad document never stops the run). `children` ingests each embedded file through
        this same graph, then links it to its parent. The input is `{doc_id, source, parent_id, child, depth}`; the
        output's `reports` is the document's `DocumentReport` followed by its children's."""
        from langgraph.graph import END, START, StateGraph

        from rag_wright.api.documents import aparse_document_bytes

        from rag_wright.capabilities.document_parse import EmbeddedChild, SourceDocument
        from rag_wright.capabilities.rlm_chunking import Chunk

        # Each node reads the typed values it needs from the state and writes built-in values back (`_dump`), so
        # the state survives any checkpointer (LGI-3).
        def document(state: _DocumentState) -> Any:
            return _load(SourceDocument, state["document"])

        def chunks_of(state: _DocumentState) -> list:
            return _load(list[Chunk], state["chunks"])

        def tagged_of(state: _DocumentState) -> list:
            return _load(list[TaggedSpan], state["tagged"])

        async def parse(state: _DocumentState) -> dict:
            src = _load(IngestSource, state["source"])
            data = src.read_bytes()
            parsed = await aparse_document_bytes(state["doc_id"], src.display_name, data,
                                                 cache_dir=stages.cache / "parsed",
                                                 include_hidden_sheets=src.include_hidden_sheets, tuning=stages.tuning)
            return {"document": _dump(SourceDocument, parsed), "sha256": hashlib.sha256(data).hexdigest()}

        async def chunk(state: _DocumentState) -> dict:
            return {"chunks": _dump(list[Chunk], await stages.chunk(document(state)))}

        async def segment(state: _DocumentState) -> dict:
            return {"tagged": _dump(list[TaggedSpan], await stages.segment(document(state), chunks_of(state)))}

        async def index(state: _DocumentState) -> dict:
            return {"indexed": await stages.index(document(state), tagged_of(state), chunks_of(state))}

        async def extract(state: _DocumentState) -> dict:
            stage = await stages.extract(document(state), tagged_of(state), table_mode=state["source"]["table_mode"])
            return {"extracted": _dump_extract_stage(stage)}

        async def write(state: _DocumentState) -> dict:
            extracted = _load_extract_stage(state["extracted"])
            return {"records": await stages.write(document(state), extracted.extractions)}

        async def document_hook(state: _DocumentState) -> dict:
            if self._document_hook is not None:
                await self._document_hook(ws, document(state), chunks_of(state))
            return {}

        async def document_node(state: _DocumentState) -> dict:
            src = _load(IngestSource, state["source"])
            child = None if state.get("child") is None else _load(EmbeddedChild, state["child"])
            name = src.display_name
            await asyncio.to_thread(
                stages.write_document_node, state["doc_id"], parent_id=state.get("parent_id"),
                filename=(child.filename if child is not None else None) or name,
                media_type=child.media_type if child is not None else (mimetypes.guess_type(name)[0] or ""),
                sha256=child.sha256 if child is not None else state["sha256"], metadata=src.metadata)
            return {}

        async def children(state: _DocumentState) -> dict:
            doc_id, src, sd, depth = state["doc_id"], _load(IngestSource, state["source"]), document(state), \
                state.get("depth", 0)
            extracted, indexed = _load_extract_stage(state["extracted"]), state["indexed"]
            rep = _document_report(state)
            rep.chunks, rep.units = len(state["chunks"]), len(extracted.units)
            rep.spans, rep.span_failures = indexed["span_count"], indexed["span_failures"]
            rep.extraction_failures = extracted.failures
            reports = [rep]
            for j, emb in enumerate(sd.embedded, 1):
                if depth + 1 > _MAX_CHILD_DEPTH:
                    rep.embedded_skipped.append(f"{emb.doc_id}: nested deeper than {_MAX_CHILD_DEPTH}")
                    continue
                emb_src = IngestSource(path=emb.path, doc_id=emb.doc_id, table_mode=src.table_mode,
                                       include_hidden_sheets=src.include_hidden_sheets, metadata=src.metadata)
                kids = await self._ingest_file(ws, stages, emb.doc_id, emb_src, parent_id=doc_id, child=emb,
                                               depth=depth + 1, graph=compiled)
                reports.extend(kids)
                if kids[0].dead_letter is None:
                    rep.children.append(emb.doc_id)
                    await asyncio.to_thread(stages.write_child_links, doc_id, emb, extracted.row_spans, rep)
                self._progress(f"[ingest]   {doc_id} child {j}/{len(sd.embedded)} {emb.filename or emb.doc_id} "
                               + (f"DEAD-LETTER: {kids[0].dead_letter}" if kids[0].dead_letter else
                                  f"spans={kids[0].spans} units={kids[0].units}"))
            return {"reports": _dump(list[DocumentReport], reports)}

        def dead_letter(state: _DocumentState) -> dict:
            rep = _document_report(state)
            rep.dead_letter = state["dead_letter"]
            return {"reports": _dump(list[DocumentReport], [rep])}

        stages_in_order = [("parse", parse), ("chunk", chunk), ("segment", segment), ("index", index),
                           ("extract", extract), ("write", write), ("document_hook", document_hook),
                           ("document_node", document_node)]
        g = StateGraph(_DocumentState)
        for (name, fn), nxt in zip(stages_in_order, [n for n, _ in stages_in_order[1:]] + ["children"]):
            g.add_node(name, _dead_lettering(fn))
            g.add_conditional_edges(name, functools.partial(_next_unless_dead, nxt), [nxt, "dead_letter"])
        g.add_node("children", children)
        g.add_node("dead_letter", dead_letter)
        g.add_edge(START, "parse")
        g.add_edge("children", END)
        g.add_edge("dead_letter", END)
        compiled = g.compile(name="document_ingestion")
        return compiled


def _sources(sources: Sequence[Union[str, Path, IngestSource]]) -> list[IngestSource]:
    return [s if isinstance(s, IngestSource) else IngestSource(path=str(s)) for s in sources]


@functools.cache
def _adapter(kind: Any) -> Any:
    from pydantic import TypeAdapter

    return TypeAdapter(kind)


def _dump(kind: Any, value: Any) -> Any:
    """A typed value as built-in values, for the graph state (any checkpointer can store and load it)."""
    return _adapter(kind).dump_python(value)


def _load(kind: Any, value: Any) -> Any:
    """The typed value back from the graph state."""
    return _adapter(kind).validate_python(value)


def _dump_extract_stage(stage: ExtractStage) -> dict:
    return {"units": _dump(list[Unit], stage.units), "extractions": _dump(list[UnitExtraction], stage.extractions),
            "failures": stage.failures, "row_spans": [[ref, row, span] for (ref, row), span in stage.row_spans.items()]}


def _load_extract_stage(value: dict) -> ExtractStage:
    return ExtractStage(units=_load(list[Unit], value["units"]),
                        extractions=_load(list[UnitExtraction], value["extractions"]), failures=value["failures"],
                        row_spans={(ref, row): span for ref, row, span in value["row_spans"]})


class _RunState(TypedDict, total=False):
    """The run graph's state, in built-in values: the sources, each document's reports (in source order) and the
    final report."""

    sources: list  # each an `IngestSource`
    documents: Annotated[list, operator.add]  # one entry per source: its document's reports, children included
    report: dict  # the `IngestionReport`


class _SourceState(TypedDict):
    """What the run graph sends to `document`: one source, and the run's size for the `i/N` progress line."""

    source: dict  # an `IngestSource`
    total: int


class _DocumentState(TypedDict, total=False):
    """The per-document graph's state, in built-in values. The parse itself stays in the parse cache: `document`
    holds its handle."""

    doc_id: str
    source: dict  # the `IngestSource`
    parent_id: Optional[str]
    child: Optional[dict]  # the `EmbeddedChild` this document was extracted as (None for a top-level source)
    depth: int
    document: dict  # the `SourceDocument`
    sha256: str
    chunks: list  # `Chunk`s
    tagged: list  # `TaggedSpan`s
    indexed: dict
    extracted: dict  # the `ExtractStage`
    records: int
    dead_letter: Optional[str]
    reports: list  # `DocumentReport`s


def _dead_lettering(fn: Callable[[Any], Awaitable[dict]]) -> Callable[[Any], Awaitable[dict]]:
    """A stage node whose failure is recorded as the document's dead letter instead of raised."""

    @functools.wraps(fn)
    async def node(state: Any) -> dict:
        try:
            return await fn(state)
        except Exception as exc:  # noqa: BLE001 - one bad document is dead-lettered, never the whole run
            return {"dead_letter": f"{type(exc).__name__}: {exc}"}

    return node


def _next_unless_dead(nxt: str, state: Any) -> str:
    return "dead_letter" if state.get("dead_letter") else nxt


def _document_report(state: Any) -> DocumentReport:
    """The document's report from what its stages recorded so far (a dead-lettered document keeps what landed)."""
    sd = state.get("document")
    return DocumentReport(doc_id=state["doc_id"], parent_doc_id=state.get("parent_id"),
                          records=state.get("records", 0),
                          skipped_hidden_sheets=list(sd["skipped_hidden_sheets"]) if sd is not None else [],
                          embedded_skipped=list(sd["embedded_skipped"]) if sd is not None else [])


def _with_pages(document: Any, chunks: list, chunk: Any, spans: list[Span]) -> list[Span]:
    """Best-effort page/bbox provenance for each span (issue 0032); unchanged when the parse carries no pages."""
    if chunk.doc_start is None:
        return spans
    try:
        from rag_wright.capabilities.rlm_chunking import canonical_document_text
        from rag_wright.corpus.document_parser import content_items
        from rag_wright.spans.page_map import build_page_offset_map, pages_for

        page_map = build_page_offset_map(content_items(document), canonical_document_text(chunks))
    except Exception:  # noqa: BLE001 - provenance is best-effort
        return spans
    if not page_map:
        return spans
    out = []
    for s in spans:
        pages, bbox = pages_for(page_map, chunk.doc_start + s.start, chunk.doc_start + s.end)
        out.append(s.model_copy(update={"pages": pages, "bbox": bbox}))
    return out


def _row_spans(document: Any, spans: list[Span]) -> dict:
    """(table self-ref, 0-based grid row) -> the span holding that row. A table's chunk text renders one line per grid
    row (header, a separator, then the data rows -- the same renderer `content_items` uses), so a row is matched to
    the first unused span whose first table line is that row's rendered line."""
    from rag_wright.corpus.document_parser import _table_content_text
    from rag_wright.ingestion.tables import visible_tables

    by_line: dict[str, list[str]] = {}
    for s in spans:
        lines = [ln.strip() for ln in s.text.strip().split("\n") if ln.strip().startswith("|")]
        if lines:
            by_line.setdefault(lines[0], []).append(s.span_id)
    out: dict = {}
    for table in visible_tables(document):
        lines = [ln.strip() for ln in _table_content_text(table, document).split("\n") if ln.strip().startswith("|")]
        rows = lines[:1] + lines[2:]  # drop the separator line: rows[i] = grid row i
        for i, line in enumerate(rows):
            ids = by_line.get(line)
            if ids:
                out[(table.self_ref, i)] = ids.pop(0)
    return out


def _attach_table_rows(document: Any, units: list, row_spans: dict) -> None:
    """ING-7: a unit holding exactly ONE data row of a parsed table carries that row (`Unit.table_row`, exact cells
    from the grid)."""
    from rag_wright.ingestion.tables import rows_of, visible_tables

    span_row = {sid: key for key, sid in row_spans.items() if key[1] > 0}
    if not span_row:
        return
    rows = {(r.table_ref, r.row_index): r for t in visible_tables(document) for r in rows_of(t, document)}
    for unit in units:
        keys = {span_row[s.span_id] for s in unit.spans if s.span_id in span_row}
        if len(keys) == 1:
            unit.table_row = rows.get(keys.pop())


def _check_model_ceilings(tuning: IngestionTuning, documents: int, progress: Callable[[str], Any]) -> None:
    """PS-21: warn when this ingest's extractor fan-out can exceed a resolved model's server ceiling. The peak is
    `min(document_concurrency, documents) x extract_concurrency`; a model's ceiling is its profile's
    `max_concurrency` (a deployed server registers its `max_num_seqs`). Calls of parallel `aingest` runs add up."""
    from rag_wright.models.profiles import ModelRole, model_for, profile_for

    peak = min(tuning.document_concurrency, max(documents, 1)) * tuning.extract_concurrency
    roles: dict[str, list[str]] = {}
    for role in ModelRole:
        roles.setdefault(model_for(role), []).append(role.value)
    for model_id, names in roles.items():
        ceiling = profile_for(model_id).max_concurrency
        if ceiling is not None and peak > ceiling:
            message = (f"this ingest can run up to {peak} extractor calls at once (min(document_concurrency, "
                       f"documents) x extract_concurrency); model {model_id} (roles: {', '.join(names)}) accepts "
                       f"{ceiling} at once. If the extractor calls it, lower the tuning or deploy with a higher "
                       f"max_num_seqs; parallel aingest runs add up")
            progress(f"[ingest] warning: {message}")
            warnings.warn(message, RuntimeWarning, stacklevel=3)


def build_ingestion(extractor: Extractor, *, segmenter: Optional[Segmenter] = None,
                    span_tagger: Optional[SpanTagger] = None, unit_grouper: Optional[UnitGrouper] = None,
                    boundary_decider: Optional[BoundaryDecider] = None, writer: Optional[RecordWriter] = None,
                    tuning: Optional[IngestionTuning] = None, embedder: Any = None, chunk_model: Optional[str] = None,
                    progress: Callable[[str], Any] = functools.partial(print, flush=True),
                    document_hook: Optional[DocumentHook] = None,
                    unit_representative: Optional[UnitRepresentative] = None,
                    chunk_discoverer: Any = None) -> IngestionPipeline:
    """The engine's generic ingestion pipeline: pass your `extractor` (a `Unit` -> `UnitExtraction`) and override
    any other hook you need; `tuning` sets the thresholds of the default hooks. `embedder` (an `encode_batch`
    object) defaults to the workspace's ingest embedder; `chunk_model` is used only to refine an over-cap section.
    `document_hook(ws, source_document, chunks)` is awaited once per document after its records are written (e.g. a
    domain's entity graph); its return value is ignored. `boundary_decider` (a `BoundaryDecider`: candidate line
    texts -> "starts a new unit?" per text) is handed to the unit grouper to settle the boundaries its rules are
    unsure of; None = the grouper's own rules only. `unit_representative` (a `UnitRepresentative`: a unit's member
    spans -> the one that represents it) sets each unit's `anchor` and leading tag after grouping -- e.g. the
    operative sentence rather than a heading; None = the grouper's own choice. `chunk_discoverer` (a
    `BoundaryDiscoverer`) sets the chunk-boundary rule; None = the engine default (structural boundaries, `chunk_model`
    refining only over-cap sections), and `default_chunk_discoverer(guidance=...)` is that default with your domain's
    wording. Returns an `IngestionPipeline`; run it with
    `await pipeline.aingest(ws, sources, cache_dir=...)`."""
    t = tuning or IngestionTuning()
    if t.extract_concurrency < 1 or t.document_concurrency < 1:
        raise ValueError("extract_concurrency and document_concurrency must be >= 1")
    return IngestionPipeline(extractor, segmenter=segmenter, span_tagger=span_tagger, unit_grouper=unit_grouper,
                             boundary_decider=boundary_decider, writer=writer, tuning=tuning, embedder=embedder,
                             chunk_model=chunk_model, progress=progress, document_hook=document_hook,
                             unit_representative=unit_representative, chunk_discoverer=chunk_discoverer)


def default_chunk_discoverer(chunk_model: Optional[str] = None, *, guidance: Optional[str] = None) -> Any:
    """PS-R5b: the engine's default chunk-boundary rule (structural boundaries first; `chunk_model` refines only an
    over-cap section, through a domain-neutral prompt) with your domain's `guidance` added to that prompt (e.g. what a
    coherent unit is in your documents). Pass it as `build_ingestion(chunk_discoverer=...)`."""
    from rag_wright.capabilities.rlm_chunking import StructuralModelFallbackDiscoverer

    return StructuralModelFallbackDiscoverer(chunk_model, guidance=guidance)


__all__ = ["build_ingestion", "default_chunk_discoverer", "IngestionPipeline", "IngestionStages", "ExtractStage", "IngestionReport", "DocumentReport"]
