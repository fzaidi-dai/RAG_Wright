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
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Sequence, Union

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
                            sha256: str) -> None:
        self.store.kg_write([KgNode("Document", "doc_id", {
            "doc_id": doc_id, "parent_doc_id": parent_id or "", "filename": filename, "media_type": media_type,
            "sha256": sha256})])

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
                      cache_dir: Union[str, Path]) -> IngestionReport:
        """Ingest every source (and its embedded children) into the workspace `ws` (from `open_workspace`).
        `sources` are file paths or `IngestSource`s (a path, or in-memory bytes with a file name); `cache_dir` holds the content-hash-gated parse and chunk caches
        (a re-ingest of an unchanged file re-uses them). Documents run concurrently up to
        `tuning.document_concurrency`; a document that fails is dead-lettered in its `DocumentReport`, never raised.
        Returns the `IngestionReport`."""
        stages = self.stages(ws, cache_dir=cache_dir)
        items = [s if isinstance(s, IngestSource) else IngestSource(path=str(s)) for s in sources]
        report = IngestionReport()
        sem = asyncio.Semaphore(stages.tuning.document_concurrency)
        done = 0
        self._progress(f"[ingest] start N={len(items)}")

        async def one(src: IngestSource) -> None:
            nonlocal done
            async with sem:
                docs = await self._ingest_file(ws, stages, src.doc_id or _doc_id(src.display_name), src,
                                               parent_id=None, child=None, depth=0)
            report.documents.extend(docs)
            done += 1
            top = docs[0]
            self._progress(f"[ingest] {done}/{len(items)} {top.doc_id} "
                           + (f"DEAD-LETTER: {top.dead_letter}" if top.dead_letter else
                              f"spans={top.spans} units={top.units} records={top.records} "
                              f"children={len(top.children)} failures={len(top.extraction_failures)}"))

        await asyncio.gather(*(one(s) for s in items))
        self._progress(f"[ingest] done {report.succeeded} ok, {report.failed} dead-lettered")
        return report

    async def _ingest_file(self, ws: Any, stages: IngestionStages, doc_id: str, src: IngestSource, *,
                           parent_id: Optional[str], child: Optional[Any], depth: int) -> list[DocumentReport]:
        """Ingest one document (`child` = its `EmbeddedChild` record when it was extracted from `parent_id`), then
        its own embedded children."""
        from rag_wright.api.documents import aparse_document_bytes

        rep = DocumentReport(doc_id=doc_id, parent_doc_id=parent_id)
        try:
            name, data = src.display_name, src.read_bytes()
            sd = await aparse_document_bytes(doc_id, name, data, cache_dir=stages.cache / "parsed",
                                             include_hidden_sheets=src.include_hidden_sheets, tuning=stages.tuning)
            rep.skipped_hidden_sheets = list(sd.skipped_hidden_sheets)
            rep.embedded_skipped = list(sd.embedded_skipped)
            chunks = await stages.chunk(sd)
            tagged = await stages.segment(sd, chunks)
            indexed = await stages.index(sd, tagged, chunks)
            extracted = await stages.extract(sd, tagged, table_mode=src.table_mode)
            rep.records = await stages.write(sd, extracted.extractions)
            if self._document_hook is not None:
                await self._document_hook(ws, sd, chunks)
            await asyncio.to_thread(
                stages.write_document_node, doc_id, parent_id=parent_id,
                filename=(child.filename if child is not None else None) or name,
                media_type=child.media_type if child is not None else (mimetypes.guess_type(name)[0] or ""),
                sha256=child.sha256 if child is not None else hashlib.sha256(data).hexdigest())
        except Exception as exc:  # noqa: BLE001 - one bad document is dead-lettered, never the whole run
            rep.dead_letter = f"{type(exc).__name__}: {exc}"
            return [rep]
        rep.chunks, rep.units = len(chunks), len(extracted.units)
        rep.spans, rep.span_failures = indexed["span_count"], indexed["span_failures"]
        rep.extraction_failures = extracted.failures
        reports = [rep]
        for j, emb in enumerate(sd.embedded, 1):
            if depth + 1 > _MAX_CHILD_DEPTH:
                rep.embedded_skipped.append(f"{emb.doc_id}: nested deeper than {_MAX_CHILD_DEPTH}")
                continue
            emb_src = IngestSource(path=emb.path, doc_id=emb.doc_id, table_mode=src.table_mode,
                                   include_hidden_sheets=src.include_hidden_sheets)
            kids = await self._ingest_file(ws, stages, emb.doc_id, emb_src,
                                           parent_id=doc_id, child=emb, depth=depth + 1)
            reports.extend(kids)
            if kids[0].dead_letter is None:
                rep.children.append(emb.doc_id)
                await asyncio.to_thread(stages.write_child_links, doc_id, emb, extracted.row_spans, rep)
            self._progress(f"[ingest]   {doc_id} child {j}/{len(sd.embedded)} {emb.filename or emb.doc_id} "
                           + (f"DEAD-LETTER: {kids[0].dead_letter}" if kids[0].dead_letter else
                              f"spans={kids[0].spans} units={kids[0].units}"))
        return reports


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
