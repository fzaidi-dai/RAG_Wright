"""Generic document-parse surface (EP-API-6b): the corpus-seam `SourceDocument` contract + the docling-parse
helpers that build one from raw bytes.

DOMAIN-FREE on purpose. It uses only the generic docling parser (`capabilities.parsing`) and the generic text
extraction (`corpus.document_parser`) -- nothing contract/clause/edgar -- so the engine's public parse API
(`api.parse_document`) depends on THIS, not on the contract ingestion pipeline (which would drag the whole
contract reference pack into any caller just to parse a PDF). A byte-source corpus adapter for ANY domain builds
a structure-bearing `SourceDocument` the same way. (Relocated out of `packs/contracts/subgraphs/contract_ingestion_pipeline`,
which now re-exports these for its existing importers.)
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel

from rag_wright.capabilities.parsing import ParsedDocument
from rag_wright.contracts.provenance import ConfidenceTag

_INGEST_PARSE_DEADLINE_S = 600.0  # per-document parse ceiling (a degraded multi-page doc escalated to the VLM)


class ChildAnchor(BaseModel):
    """ING-6: where an embedded child sits in its parent. For a spreadsheet, also the parsed TABLE (`table_ref`, a
    docling self-ref) and 0-based `table_row` the anchor cell falls in -- the record the child belongs to."""

    sheet: Optional[str] = None
    cell: Optional[str] = None
    row: Optional[int] = None
    col: Optional[int] = None
    paragraph: Optional[int] = None
    slide: Optional[int] = None
    table_ref: Optional[str] = None
    table_row: Optional[int] = None


class RecordLink(BaseModel):
    """ING-6: a record (parsed table row) an embedded child belongs to, with how sure we are (FR-S.4). `basis` is
    `anchor` (the row its icon sits on) or `content` (the row's identifiers appear in the child); `evidence` lists
    the identifier tokens that matched (empty for a position-only link)."""

    table_ref: str
    table_row: int
    confidence: ConfidenceTag
    basis: Literal["anchor", "content"]
    evidence: list[str] = []


class EmbeddedChild(BaseModel):
    """ING-6: a file embedded in the parent (e.g. a lab-report PDF in a spreadsheet cell), extracted to the parse
    cache as its own document. `doc_id` = `<parent_id>.emb.<sha12>`; `filename` is the original name (metadata
    only); `path` is the stored copy (never in the source folder)."""

    doc_id: str
    path: str
    filename: Optional[str] = None
    media_type: str
    sha256: str
    anchors: list[ChildAnchor] = []
    links: list[RecordLink] = []  # the record(s) it belongs to; empty = no record found (still a child document)


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
    # ING-4a: hidden spreadsheet sheets NOT ingested because the caller chose to skip them (reported, never silent).
    skipped_hidden_sheets: list[str] = []
    # ING-6: files embedded in the parent package, extracted as child documents, and those that could not be.
    embedded: list[EmbeddedChild] = []
    embedded_skipped: list[str] = []


def _hidden_sheets(document: Any) -> list:
    """ING-4a: a spreadsheet parse's hidden worksheets (docling puts them in the INVISIBLE content layer)."""
    from docling_core.types.doc.document import ContentLayer

    return [g for g in getattr(document, "groups", []) or []
            if getattr(g.label, "value", g.label) == "sheet" and g.content_layer == ContentLayer.INVISIBLE]


def _include_hidden_sheets(document: Any) -> bool:
    """ING-4a: move every hidden worksheet (and everything under it) into the BODY layer, so the whole engine reads
    it like a visible sheet. Returns whether anything changed."""
    from docling_core.types.doc.document import ContentLayer

    sheets = _hidden_sheets(document)
    for sheet in sheets:
        sheet.content_layer = ContentLayer.BODY
        for node, _level in document.iterate_items(root=sheet, with_groups=True,
                                                    included_content_layers=set(ContentLayer)):
            node.content_layer = ContentLayer.BODY
    return bool(sheets)


def parsed_source_document(
    source_doc_id: str, name: str, data: bytes, *, cache_dir: Any, metadata: Optional[dict] = None,
    include_hidden_sheets: bool = True, tuning: Optional[Any] = None,
) -> SourceDocument:
    """Build a STRUCTURE-BEARING `SourceDocument` from raw document BYTES (PDF/DOCX/HTML/MD): docling-parse ONCE
    (content-hash gated + cached), carry the `DoclingDocument` on `.parsed` (so the chunker's structural pass
    fires on real headings), and set `.text` to the flattened text (for the text-consuming stages). This is how a
    byte-source corpus adapter -- or the product (RuleWright), which hand-builds its ingest -- feeds a real
    document to the engine; a plain-text `SourceDocument` (no `.parsed`) still uses the text fallback.

    ING-4a: a spreadsheet's HIDDEN sheets are ingested by default; `include_hidden_sheets=False` skips them and lists
    them in `skipped_hidden_sheets`. The choice is part of the parse cache key."""
    import hashlib
    import json
    from pathlib import Path

    from rag_wright.capabilities.parsing import TieredOCRParser, load_document
    from rag_wright.corpus.document_parser import document_to_text, parse_document_bytes

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    content_hash = hashlib.sha256(data).hexdigest()
    variant = "" if include_hidden_sheets else ".nohidden"  # ING-4a: the hidden-sheet choice is in the cache key
    manifest_path = cache_dir / f"{source_doc_id}.{content_hash[:16]}{variant}.json"
    ocr_sidecar = cache_dir / f"{source_doc_id}.{content_hash[:16]}.ocr.json"  # the OCR verdict, cached alongside
    parsed = ParsedDocument(
        source_doc_id=source_doc_id, content_hash=content_hash, manifest_path=str(manifest_path))
    if manifest_path.exists():  # content-hash gate: parse once (restore the OCR verdict from the sidecar)
        document = load_document(parsed)
        unreadable = json.loads(ocr_sidecar.read_text()) if ocr_sidecar.exists() else []
        if include_hidden_sheets and _include_hidden_sheets(document):
            document.save_as_json(manifest_path)  # a cache written before ING-4a: normalize it once
    else:  # 0009-WIRE2: tiered OCR -- capture which pages stayed unreadable even after VLM, so they surface PARTIAL
        tiered = TieredOCRParser()
        document = parse_document_bytes(name, data, parser=tiered)
        if include_hidden_sheets:
            _include_hidden_sheets(document)
        document.save_as_json(manifest_path)
        unreadable = list(tiered.report.unreadable_pages)
        ocr_sidecar.write_text(json.dumps(unreadable))
    embedded, embedded_skipped = _extract_children(
        source_doc_id, name, data, document, cache_dir / f"{source_doc_id}.{content_hash[:16]}.embedded",
        identifier=getattr(tuning, "identifier", None))
    return SourceDocument(
        source_doc_id=source_doc_id, text=document_to_text(document), parsed=parsed, metadata=metadata or {},
        ocr_unreadable_pages=unreadable,
        skipped_hidden_sheets=[] if include_hidden_sheets else [g.name for g in _hidden_sheets(document)],
        embedded=embedded, embedded_skipped=embedded_skipped)


_MEDIA_SUFFIX = {
    "application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
}


def _table_at(document: Any, sheet: Optional[str], row: Optional[int], col: Optional[int]) -> tuple:
    """ING-6: the parsed table (self-ref) on `sheet` whose sheet-coordinate box holds cell (row, col), and the
    0-based table row -- docling records a spreadsheet table's position in cells (`prov[0].bbox`)."""
    if sheet is None or row is None or col is None:
        return None, None
    for group in getattr(document, "groups", []) or []:
        if getattr(group.label, "value", group.label) != "sheet" or group.name != sheet:
            continue
        for ref in group.children:
            item = ref.resolve(document)
            if getattr(item.label, "value", item.label) != "table" or not item.prov:
                continue
            box = item.prov[0].bbox
            if box.l <= col < box.r and box.t <= row < box.b:
                return item.self_ref, row - int(box.t)
    return None, None


def _extract_children(source_doc_id: str, name: str, data: bytes, document: Any, store: Any, *,
                      identifier: Optional[Any] = None) -> tuple[list, list]:
    """ING-6: extract the package's embedded files into `store` (content-addressed, so a re-parse writes nothing
    new and identical files are stored once) and describe them as `EmbeddedChild`ren with resolved anchors."""
    from rag_wright.corpus.embedded import extract_embedded, file_text

    found = extract_embedded(name, data)
    children = []
    for f in found.files:
        suffix = _MEDIA_SUFFIX.get(f.media_type) or (Path(f.filename).suffix if f.filename else "") or ".bin"
        path = store / f"{f.sha256[:12]}{suffix}"
        if not path.exists():
            store.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f.data)
        anchors = []
        for a in f.anchors:
            table_ref, table_row = _table_at(document, a.sheet, a.row, a.col)
            anchors.append(ChildAnchor(sheet=a.sheet, cell=a.cell, row=a.row, col=a.col, paragraph=a.paragraph,
                                       slide=a.slide, table_ref=table_ref, table_row=table_row))
        children.append(EmbeddedChild(doc_id=f"{source_doc_id}.emb.{f.sha256[:12]}", path=str(path),
                                      filename=f.filename, media_type=f.media_type, sha256=f.sha256, anchors=anchors))
    _link_records(document, children, [f"{file_text(f.data, f.media_type)} {f.filename or ''}" for f in found.files],
                  identifier=identifier)
    return children, list(found.skipped)


def _link_records(document: Any, children: list, texts: list[str], *, identifier: Optional[Any] = None) -> None:
    """ING-6: attach each child's record links. Its anchor row is VERIFIED (EXTRACTED) when one of the row's
    identifiers appears in the child, and the record's other rows sharing that identifier follow (INFERRED).
    Otherwise every row whose identifiers the child mentions, anywhere in the workbook, is a candidate: one
    identifier set -> INFERRED, several -> AMBIGUOUS. With no evidence, a child alone in its cell keeps a
    position-only INFERRED link; a STACKED one (several children in one cell) gets none -- it stays a child
    document, its anchor kept as provenance. Identifiers are tokens rare in the tables and among the files."""
    from collections import Counter

    from rag_wright.contracts.ingestion import IdentifierRule
    from rag_wright.corpus.embedded import candidate_tokens

    rule = identifier or IdentifierRule()

    rows = [(t.self_ref, i, candidate_tokens(" ".join(c.text for c in r)))
            for t in getattr(document, "tables", []) or [] for i, r in enumerate(t.data.grid[1:], 1)]
    if not rows or not children:
        return
    child_tokens = [candidate_tokens(t) for t in texts]
    row_df = Counter(tok for _ref, _i, toks in rows for tok in toks)
    file_df = Counter(tok for toks in child_tokens for tok in toks)

    def ids(toks: set) -> set:
        return {t for t in toks if row_df[t] <= rule.max_rows and file_df[t] <= rule.max_files}

    per_cell = Counter((a.sheet, a.cell) for c in children for a in c.anchors if a.cell)
    for child, toks in zip(children, child_tokens):
        links: dict[tuple, RecordLink] = {}
        verified: set = set()
        for a in child.anchors:
            own = next((rt for ref, i, rt in rows if ref == a.table_ref and i == a.table_row), None)
            hit = ids(own) & toks if own is not None else set()
            if hit:
                verified |= hit
                links[(a.table_ref, a.table_row)] = RecordLink(table_ref=a.table_ref, table_row=a.table_row,
                                                               confidence=ConfidenceTag.EXTRACTED, basis="anchor",
                                                               evidence=sorted(hit))
        matches = {(ref, i): ids(rt) & toks for ref, i, rt in rows if ids(rt) & toks}
        if verified:
            matches = {k: v for k, v in matches.items() if v & verified}
        sets = {frozenset(v) for v in matches.values()}
        confidence = ConfidenceTag.INFERRED if verified or len(sets) == 1 else ConfidenceTag.AMBIGUOUS
        for (ref, i), hit in matches.items():
            links.setdefault((ref, i), RecordLink(table_ref=ref, table_row=i, confidence=confidence, basis="content",
                                                  evidence=sorted(hit)))
        if not links:
            for a in child.anchors:
                if a.table_ref is not None and per_cell[(a.sheet, a.cell)] == 1:
                    links[(a.table_ref, a.table_row)] = RecordLink(table_ref=a.table_ref, table_row=a.table_row,
                                                                   confidence=ConfidenceTag.INFERRED, basis="anchor")
        child.links = sorted(links.values(), key=lambda lk: (lk.table_ref, lk.table_row))


async def aparsed_source_document(
    source_doc_id: str, name: str, data: bytes, *, cache_dir: Any, metadata: Optional[dict] = None,
    deadline_s: float = _INGEST_PARSE_DEADLINE_S, include_hidden_sheets: bool = True, tuning: Optional[Any] = None,
) -> SourceDocument:
    """The ASYNC, deadline-bounded twin of `parsed_source_document` (ADR-0057) -- STABLE PUBLIC API. Runs the sync
    build (docling parse + the tiered OCR/VLM escalation, the slowest call in the pipeline) OFF the event loop
    (`to_thread`) under an `asyncio.timeout`, so a hand-built async ingest can parse a document into the
    structure-bearing `SourceDocument` the chunker needs WITHOUT reimplementing the wrapper (or blocking the loop).
    Same caveat as every `to_thread` bound: the deadline unblocks the CALLER; the docling worker thread finishes in
    the background (true cancellation would route the vision call through the async model seam)."""
    async with asyncio.timeout(deadline_s):
        return await asyncio.to_thread(
            parsed_source_document, source_doc_id, name, data, cache_dir=cache_dir, metadata=metadata,
            include_hidden_sheets=include_hidden_sheets, tuning=tuning)


def parsed_text_document(source_doc_id: str, text: str, parse_dir: Any):
    """text -> a `ParsedDocument` (one TextItem per non-blank line), cached -- so the standard `chunk()` path
    (which loads a real DoclingDocument) works from a text-only source. ING-4c: moved here from the contract
    pipeline (generic mechanism; the contract pipeline re-exports it as `_parsed_from_text`)."""
    import hashlib

    from docling_core.types.doc.document import DoclingDocument
    from docling_core.types.doc.labels import DocItemLabel

    from rag_wright.capabilities.parsing import ParsedDocument

    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    parse_dir = Path(parse_dir)
    parse_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = parse_dir / f"{source_doc_id}.{content_hash[:16]}.json"
    if not manifest_path.exists():
        doc = DoclingDocument(name=source_doc_id)
        for line in text.split("\n"):
            if line.strip():
                doc.add_text(label=DocItemLabel.TEXT, text=line)
        doc.save_as_json(manifest_path)
    return ParsedDocument(source_doc_id=source_doc_id, content_hash=content_hash, manifest_path=str(manifest_path))
