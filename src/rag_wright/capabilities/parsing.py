"""Parsing capability (FR-C.1): source documents -> a clean structured representation, parsed once.

Docling's `DocumentConverter` turns a PDF, Office file, or scan into a `DoclingDocument` (reading
order, headings, sections, tables, OCR). Parsing is content-hash gated so a document is parsed once
and reused by chunking, embedding, and extraction: the structured representation is cached as JSON
(`DoclingDocument.save_as_json` / `load_from_json`) keyed by the source's content hash, so re-parsing
unchanged content is a cache hit and changed content re-parses.

The `DocumentConverter` sits behind a small `Parser` seam so the capability's cache/gate logic is
tested hermetically with a stub, and the real (model-loading) parse is exercised opt-in (`-m parse`).
Grounded against `docling.document_converter.DocumentConverter.convert` and
`docling_core.types.doc.document.DoclingDocument` (framework graph).
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from pathlib import Path
from typing import Protocol, runtime_checkable

from docling_core.types.doc.document import DoclingDocument
from pydantic import BaseModel, field_validator

from rag_wright.contracts.identifiers import canonical_source_doc_id

# Reused from the ChunkId scheme (T1): the delimiter-safe charset for a source_doc_id, so the id is
# citation/provenance-safe and consistent with `chunk_id`.
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


@runtime_checkable
class Parser(Protocol):
    """The document-conversion seam: turn a source path into a `DoclingDocument`."""

    def convert(self, source: Path) -> DoclingDocument: ...


class ParsedDocument(BaseModel):
    """The parsing capability's contract: a handle to the cached structured representation.

    The full `DoclingDocument` lives in the parse manifest at `manifest_path` (loaded via
    `load_document`); this record carries the identity and the content hash the pipeline gates on.
    """

    model_config = {"frozen": True}

    source_doc_id: str
    content_hash: str  # sha256 hex of the source bytes
    manifest_path: str

    @field_validator("source_doc_id")
    @classmethod
    def _safe_source_doc_id(cls, v: str) -> str:
        if not v or _SAFE.search(v):
            raise ValueError("source_doc_id must be non-empty and use only [A-Za-z0-9._-]")
        return v


_THREAD = threading.local()


def _thread_converter():
    """The calling thread's Docling `DocumentConverter`, built on first use and REUSED for every later document.
    Building one loads Docling's layout + OCR models (seconds); doing that per document made a bulk parse pay the
    load every time. Per THREAD, not process-wide: Docling locks only pipeline initialisation, not concurrent
    conversion through one pipeline, so a converter is never shared across threads (`to_thread` workers each
    keep their own)."""
    converter = getattr(_THREAD, "converter", None)
    if converter is None:
        from docling.document_converter import DocumentConverter

        converter = _THREAD.converter = DocumentConverter()
    return converter


class DoclingParser:
    """The real parser: Docling's `DocumentConverter` (the calling thread's, reused across documents). Built
    lazily so importing the capability (and the hermetic tests) does not load Docling's models."""

    def convert(self, source: Path) -> DoclingDocument:
        return _thread_converter().convert(source).document

    def parse_range(self, source: Path, page_range: tuple[int, int]) -> DoclingDocument:
        """PARSE-3: parse only pages `page_range` (1-based, inclusive) -- lets the tiered path fast-parse the
        born-digital pages and VLM only the degraded ones, then concatenate, instead of VLM-ing the whole doc."""
        return _thread_converter().convert(source, page_range=page_range).document


class TieredOCRReport(BaseModel):
    """0009-WIRE: which pages the tiered parser escalated to the VLM, and which remained unreadable even after
    the VLM (genuine info loss -> the caller should flag PARTIAL / needs-rescan)."""

    escalated_pages: list[int] = []
    unreadable_pages: list[int] = []


class TieredOCRParser:
    """0009-WIRE: fast OCR -> scan-quality gate -> VLM escalation for degraded pages -> PARTIAL for what the VLM
    still cannot read. A `Parser`, so it drops into `parse(..., parser=TieredOCRParser())` unchanged.

    Benchmark (docs/eval/ocr_benchmark.md): fast OCR is perfect on readable scans and worthless on a heavily
    degraded one (char_sim ~0.01); a VLM reads the degraded-but-readable scan (Gemma-4 0.991). So: run the cheap
    fast parse, and ONLY when a page's OCR is untrustworthy re-parse via the VLM (whole-document escalation --
    the VLM reads good pages fine too, so this is safe and keeps the common readable case at zero VLM cost).
    `fast` and `vlm` are `Parser`s (injectable); the last run's `report` is exposed for PARTIAL reporting."""

    def __init__(self, *, fast: Parser | None = None, vlm: Parser | None = None) -> None:
        self._fast = fast
        self._vlm = vlm
        self.report = TieredOCRReport()

    def convert(self, source: Path) -> DoclingDocument:
        from rag_wright.capabilities.scan_quality import ScanQuality, assess_document

        fast = self._fast or DoclingParser()
        fast_doc = fast.convert(source)
        # 0009-GATE-CAL: fold in IMAGE metrics (blur/faintness) -- the strong signal a text-only gate misses when
        # the fast OCR is garbled-but-common-word. The VLM re-check below is text-only (the image stays blurry).
        assessed = assess_document(fast_doc, page_images=_render_gray_pages(source))
        degraded = sorted(pg for pg, a in assessed.items() if a.quality is not ScanQuality.READABLE)
        # PARSE-1: a page with a usable NATIVE text layer (born-digital) is authoritative -- the OCR word-hit gate
        # false-positives on legitimately sparse born-digital pages (a signature/joinder page: names, titles,
        # page numbers), which triggered an unnecessary whole-document VLM escalation (~minutes on OpenRouter) on
        # real contracts. So never OCR-escalate a page whose text layer we can read directly; only genuinely
        # image-only pages (no text layer) stay in the escalation set.
        born_digital = _text_layer_pages(source)
        degraded = [pg for pg in degraded if pg not in born_digital]
        if not degraded:  # readable scan OR every "degraded" page was actually born-digital -> no VLM cost
            self.report = TieredOCRReport()
            return fast_doc

        vlm = self._vlm if self._vlm is not None else (_default_vlm_parser() if _vlm_available() else None)
        if vlm is None:  # GRACEFUL DEGRADE: no VLM configured -> cannot escalate; flag PARTIAL, keep the fast doc
            self.report = TieredOCRReport(escalated_pages=[], unreadable_pages=degraded)
            _log_unreadable(source, degraded, "no VLM configured (set OPENROUTER_API_KEY)")
            return fast_doc
        try:
            # PARSE-3: escalate ONLY the degraded pages to the VLM (per-page), then concatenate with the
            # fast-parsed good pages -- never the whole document. A large born-digital doc with one genuine
            # image-only page used to VLM all 60+ pages (~30 min) and blow the 600s parse deadline.
            vlm_doc = _escalate_degraded_pages(source, fast, vlm, degraded)
        except Exception as exc:  # noqa: BLE001 - a VLM failure must not sink the parse; flag PARTIAL, keep fast doc
            self.report = TieredOCRReport(escalated_pages=degraded, unreadable_pages=degraded)
            _log_unreadable(source, degraded, f"VLM escalation failed: {exc!r}")
            return fast_doc

        vlm_assessed = assess_document(vlm_doc)
        unreadable = sorted(pg for pg in degraded
                            if vlm_assessed.get(pg) is None or vlm_assessed[pg].quality is not ScanQuality.READABLE)
        self.report = TieredOCRReport(escalated_pages=degraded, unreadable_pages=unreadable)
        if unreadable:  # even the VLM could not read these -> surface, never silently ingest gibberish
            _log_unreadable(source, unreadable, "unreadable even after VLM escalation")
        return vlm_doc


def _default_vlm_parser() -> Parser:
    from rag_wright.capabilities.vlm_ocr import VlmOCRParser

    return VlmOCRParser()


def _page_count(source: Path) -> int:
    """Total page count of a PDF (pypdfium2, no parse). 0 for a non-PDF or any read error -> the caller falls
    back to whole-document escalation."""
    try:
        import pypdfium2 as pdfium

        return len(pdfium.PdfDocument(str(source)))
    except Exception:  # noqa: BLE001 - best-effort; unknown page count -> whole-doc fallback
        return 0


def _escalation_runs(n_pages: int, degraded: set[int]) -> list[tuple[int, int, bool]]:
    """PARSE-3: partition pages 1..n_pages into CONTIGUOUS runs, each tagged `is_vlm` (a degraded page -> VLM,
    else fast). So a 63-page doc with page 31 degraded yields [(1,30,False),(31,31,True),(32,63,False)] -- the VLM
    touches only page 31. Returns `[(start, end, is_vlm)]`, 1-based inclusive, covering every page in order."""
    runs: list[tuple[int, int, bool]] = []
    start = 1
    while start <= n_pages:
        is_vlm = start in degraded
        end = start
        while end + 1 <= n_pages and ((end + 1) in degraded) == is_vlm:
            end += 1
        runs.append((start, end, is_vlm))
        start = end + 1
    return runs


def _escalate_degraded_pages(source: Path, fast: Parser, vlm: Parser, degraded: list[int]) -> DoclingDocument:
    """PARSE-3: build the escalated document by parsing each contiguous page-run with the right parser (fast for
    born-digital pages, VLM for the degraded ones) and concatenating -- so VLM cost scales with the number of
    DEGRADED pages, not the document length. Falls back to a whole-document VLM parse when the page count is
    unknown or a parser has no `parse_range` (e.g. a non-PDF, or an injected stub) -- preserving the prior
    behavior for those cases."""
    n_pages = _page_count(source)
    if not n_pages or not hasattr(fast, "parse_range") or not hasattr(vlm, "parse_range"):
        return vlm.convert(source)  # fallback: whole-document VLM (unknown page count / no page-range support)
    runs = _escalation_runs(n_pages, set(degraded))
    subdocs = [
        (vlm if is_vlm else fast).parse_range(source, (start, end)) for start, end, is_vlm in runs
    ]
    return subdocs[0] if len(subdocs) == 1 else DoclingDocument.concatenate(subdocs)


_MIN_TEXT_LAYER_CHARS = 30  # a PDF page with >= this many directly-extractable chars has a real, authoritative
#                             text layer (born-digital). Set LOW on purpose (PARSE-2, doc3): a true image-only scan
#                             page extracts ~0 chars, but a SPARSE born-digital page -- a schedule, an exhibit
#                             divider, a signature page (doc3 pages 52-58 = 91-179 chars) -- extracts only tens.
#                             The old 200 threshold mislabeled those sparse-but-real pages as scans, so a single one
#                             flagged by the OCR gate triggered a WHOLE-DOCUMENT VLM escalation that, on a 63-page
#                             doc, blew the 600s parse deadline. A real text layer of any size is authoritative.


def _text_layer_pages(source: Path, *, min_chars: int = _MIN_TEXT_LAYER_CHARS) -> set[int]:
    """PARSE-1: the 1-based page numbers of `source` that carry a usable NATIVE text layer (born-digital) -- read
    DIRECTLY from the PDF (pypdfium2, no OCR). A page here is authoritative and must never be OCR-quality-assessed
    or VLM-escalated. Best-effort: a non-PDF or any read error -> empty set (no override -> the tiered OCR path is
    unchanged), so a scan / text / office source is never affected."""
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(source))
        pages: set[int] = set()
        for i in range(len(pdf)):
            text = pdf[i].get_textpage().get_text_range()
            if len(text.strip()) >= min_chars:
                pages.add(i + 1)
        return pages
    except Exception:  # noqa: BLE001 - the text-layer probe is an optional authority signal; never fail the parse
        return set()


def _render_gray_pages(source: Path, dpi: int = 200) -> dict:
    """Render each PDF page to a grayscale image {page_no(1-based): ndarray} for the scan-quality gate's image
    metrics. 200 DPI to match the validated Laplacian/dark_frac thresholds. Best-effort: a non-PDF or any render
    error -> {} (the gate falls back to text-only), so a text/office source never breaks the parse."""
    try:
        import numpy as np
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(source))
        return {i + 1: np.asarray(pdf[i].render(scale=dpi / 72.0).to_pil().convert("L")) for i in range(len(pdf))}
    except Exception:  # noqa: BLE001 - image metrics are an optional gate signal; never fail the parse over them
        return {}


def _vlm_available() -> bool:
    """The escalation VLM is usable only if the VISION_OCR profile's endpoint is configured (an OpenRouter key, or
    a self-hosted server's base URL). Absent -> graceful degrade."""
    from rag_wright.models.profiles import ModelRole, model_for
    from rag_wright.models.seam import resolve_connection

    try:
        conn = resolve_connection(model_for(ModelRole.VISION_OCR))
    except KeyError:  # a self-hosted backend with no base URL set
        return False
    return bool(conn.api_key) or conn.backend == "ollama"


def _log_unreadable(source: Path, pages: list[int], why: str) -> None:
    """Surface unreadable/degraded pages (never silently ingest gibberish -- 0006-C / ENG-1 applied to OCR)."""
    logging.getLogger(__name__).warning(
        "[ocr] %s: pages %s could not be read (%s) -- flagged PARTIAL / needs-rescan", getattr(source, "name", source),
        pages, why)


def _source_doc_id(source: Path) -> str:
    """A delimiter-safe id from the file stem via the ONE canonical slug (HYG-1)."""
    return canonical_source_doc_id(source.stem)


def _content_hash(source: Path) -> str:
    return hashlib.sha256(source.read_bytes()).hexdigest()


def parse(source: Path, *, cache_dir: Path, parser: Parser) -> ParsedDocument:
    """Parse `source` into the cached structured representation, parsed once (content-hash gated).

    If a manifest for this content hash already exists, it is reused (no re-parse); otherwise the
    source is converted and the `DoclingDocument` is cached as JSON.
    """
    source_doc_id = _source_doc_id(source)
    content_hash = _content_hash(source)
    cache_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = cache_dir / f"{source_doc_id}.{content_hash[:16]}.json"

    if not manifest_path.exists():  # the content-hash gate: parse once
        document = parser.convert(source)
        document.save_as_json(manifest_path)

    return ParsedDocument(
        source_doc_id=source_doc_id,
        content_hash=content_hash,
        manifest_path=str(manifest_path),
    )


def load_document(parsed: ParsedDocument) -> DoclingDocument:
    """Load the full structured representation from the parse manifest."""
    return DoclingDocument.load_from_json(parsed.manifest_path)


