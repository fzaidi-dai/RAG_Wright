"""DOCPARSE-1 (ADR-0049 generic-customer lens): the generic raw-document parser -- the SHARED front-end that lets a
customer's own PDF / DOCX / HTML flow into BOTH ingestion sides.

The docling parse capability already exists (`capabilities/parsing.py`: `DocumentConverter` -> `DoclingDocument`);
this module adds (a) a BYTES entry point (customer docs arrive as bytes, e.g. from GCS, not a local path) and the
two PROJECTIONS the two sides need from a parsed document:
  - `document_to_text`  -> the contract side (`GcsCorpusAdapter.parse_bytes` seam): one text blob per document.
  - `document_to_sections` -> the compliance side (`RegulationAdapter`'s `[{section, heading, text}]` shape): the
    document split into sections at its headings, so a policy PDF ingests the same way an eCFR `sections.json` does.

The DoclingDocument API is grounded (framework graph + installed `inspect`): `export_to_markdown()` for text;
`iterate_items() -> (item, level)` with heading labels SECTION_HEADER / TITLE / FIELD_HEADING (PAGE_HEADER is
page furniture, not a section boundary)."""
from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from docling_core.types.doc.labels import DocItemLabel

# Labels that START a new section (a real heading), vs PAGE_HEADER which is running page furniture (ignored).
_HEADING_LABELS = frozenset({DocItemLabel.SECTION_HEADER, DocItemLabel.TITLE, DocItemLabel.FIELD_HEADING})
_TEXT_EXTS = frozenset({"txt", "md", "text"})


# 0009-WIRE2: a generous per-document OCR ceiling. A degraded multi-page doc escalated whole to the VLM is a
# batch op (~30s/page), so it needs more than the per-model-call deadline; per-page escalation would let us
# tighten this. Overridable via `deadline_s`.
_OCR_PARSE_DEADLINE_S = 600.0


def _default_document_parser() -> Any:
    """0009-WIRE2: the default parser for raw-document bytes is the TIERED OCR parser -- fast OCR, then a
    scan-quality gate escalates only degraded pages to the VLM (default Gemma-4 via OpenRouter), and flags what
    even the VLM cannot read. Graceful degrade when no VLM is configured. So BOTH ingestion pipelines and the MCP
    document tool get degraded-scan handling through this one chokepoint."""
    from rag_wright.capabilities.parsing import TieredOCRParser

    return TieredOCRParser()


def parse_document_bytes(name: str, data: bytes, *, parser: Any = None) -> Any:
    """Parse raw document BYTES into a `DoclingDocument`. `.txt`/`.md` bytes are wrapped directly; binary docs
    (PDF/DOCX/HTML) go through docling. `parser` defaults to the tiered OCR parser (0009-WIRE2), injectable for
    tests. `name` supplies the file extension docling needs to pick a backend."""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    parser = parser or _default_document_parser()
    # docling reads a file path, not raw bytes -> write to a temp file preserving the extension for backend choice
    suffix = f".{ext}" if ext else ".txt"
    tmp = Path(tempfile.mkdtemp(prefix="docparse_")) / f"doc{suffix}"
    tmp.write_bytes(data)
    return parser.convert(tmp)


async def aparse_document_bytes(name: str, data: bytes, *, parser: Any = None,
                                deadline_s: float = _OCR_PARSE_DEADLINE_S) -> Any:
    """ASYNC-bounded document parse (ADR-0057): run the sync `parse_document_bytes` (incl. the tiered VLM
    escalation -- the slowest call in the pipeline) OFF the event loop via `to_thread`, under a wall-clock
    `asyncio.timeout` so a hung/slow OCR never stalls the async ingestion. NOTE: `to_thread` cannot cancel the
    worker thread, so the deadline unblocks the CALLER (raises TimeoutError); the docling parse thread finishes
    in the background. True cancellation would require routing the vision call through the async model seam."""
    import asyncio

    async with asyncio.timeout(deadline_s):
        return await asyncio.to_thread(parse_document_bytes, name, data, parser=parser)


def document_to_text(doc: Any) -> str:
    """A parsed document -> one text blob (docling markdown export) -- the contract side's `parse_bytes` output."""
    return doc.export_to_markdown()


@dataclass(frozen=True)
class ContentItem:
    """issue 0014: one chunkable unit of a parsed document's READING-ORDER body. Duck-typed to a docling text
    item (`.label`, `.level`, `.text`) so the chunker/segmenter consume it unchanged. The reading-order body is a
    SUPERSET of `document.texts`: a docling TABLE lives in `document.tables` and a figure in `document.pictures`,
    NEVER in `.texts`, so a `document.texts`-only chunker silently drops them (never chunked, never indexed, never
    retrievable, and no failure recorded -- the exact 0014 loss). This projection is the single authority for
    'the document's chunkable content, in reading order'.

    issue 0032: each item also carries its parse-time PAGE provenance (`page`, 1-based, from `prov[0].page_no`)
    and, when the parser produced one, a `bbox` (l, t, r, b on `page`). This is the provenance the CU-B5 page
    map threads to a span's citation (a scanned-PDF click-through lands on the right page). `bbox` is best-effort
    -- omitted where `prov` has none, and dropped when lines are merged into a paragraph (ambiguous then)."""

    label: Any
    level: Optional[int]
    text: str
    page: Optional[int] = None  # issue 0032: 1-based source page (prov[0].page_no); None when prov is absent
    bbox: Optional[tuple[float, float, float, float]] = None  # (l, t, r, b) on `page`; only when prov has one


def _table_content_text(item: Any, doc: Any) -> str:
    """A TABLE item -> its atomic markdown (issue 0014), caption prefixed when docling captured one. The markdown
    keeps the header row with the data rows, so the fee/payment schedule retrieves as a unit."""
    caption = (item.caption_text(doc) or "").strip()
    body = (item.export_to_markdown(doc) or "").strip()
    return f"{caption}\n\n{body}".strip() if caption else body


def _picture_content_text(item: Any, doc: Any) -> str:
    """A PICTURE item -> its extractable text (issue 0014): the caption plus any description annotation
    (a VLM/description the tiered OCR attached). Empty when the figure carries no text -- nothing to index."""
    parts: list[str] = []
    caption = (item.caption_text(doc) or "").strip()
    if caption:
        parts.append(caption)
    for annotation in getattr(item, "annotations", None) or []:
        text = (getattr(annotation, "text", "") or "").strip()  # DescriptionAnnotation (figure description)
        if text:
            parts.append(text)
    return "\n\n".join(parts)


_ENDS_SENTENCE = re.compile(r"""[.:;?!]["')\]]*$""")  # a line that completes a sentence (terminal punct + closers)


def _ends_sentence(text: str) -> bool:
    return bool(_ENDS_SENTENCE.search(text.rstrip()))


def _starts_new_sentence(text: str) -> bool:
    """A line that BEGINS a new provision: its first non-space char is a capital, a digit, or an opener
    (`(`, `[`, quote, `§`, bullet). A lowercase start is a wrapped continuation ('and (ii) ...')."""
    stripped = text.lstrip()
    if not stripped:
        return False
    c = stripped[0]
    return c.isupper() or c.isdigit() or c in "([{\"'§•-"


def _join_wrapped(prev: str, nxt: str) -> str:
    """Join a wrapped continuation to its paragraph: de-hyphenate a soft line-break (`Distribu-` + `tor` ->
    `Distributor`), otherwise a single space."""
    if prev.endswith("-") and len(prev) >= 2 and prev[-2].isalpha():
        return prev[:-1] + nxt
    return f"{prev} {nxt}"


def _merge_wrapped_lines(items: list[ContentItem]) -> list[ContentItem]:
    """DEFRAG-1: reconstruct paragraphs from docling's per-LINE text items. docling emits each PDF text line as its
    own item; joined with `\\n\\n` and split by `segment_clause`, one clause shatters into per-line fragments that
    then fail extraction (NEONSYSTEMS: 219 segments / 121 degenerate -> 87 / 9 after this pass). Consecutive
    `TEXT` items are merged into one paragraph, breaking ONLY when the previous line ends a sentence AND the next
    starts one (two-sided, so an abbreviation like 'Inc.' followed by a lowercase 'and' does not false-split, and a
    clean paragraph-per-item document is left untouched). Any NON-text item (heading, table, figure, list item,
    page furniture) is a hard boundary -- never merged across."""
    out: list[ContentItem] = []
    buf: str = ""
    buf_level: Optional[int] = None
    buf_page: Optional[int] = None  # issue 0032: the merged paragraph keeps its FIRST line's page (bbox dropped)
    for item in items:
        if item.label != DocItemLabel.TEXT:  # heading / table / picture / list-item / furniture -> hard boundary
            if buf.strip():
                out.append(ContentItem(label=DocItemLabel.TEXT, level=buf_level, text=buf, page=buf_page))
            buf, buf_level, buf_page = "", None, None
            out.append(item)
            continue
        line = (item.text or "").strip()
        if not line:
            continue
        if not buf:
            buf, buf_level, buf_page = line, item.level, item.page
        elif _ends_sentence(buf) and _starts_new_sentence(line):  # a real paragraph break
            out.append(ContentItem(label=DocItemLabel.TEXT, level=buf_level, text=buf, page=buf_page))
            buf, buf_level, buf_page = line, item.level, item.page
        else:  # a wrapped continuation of the same clause
            buf = _join_wrapped(buf, line)
    if buf.strip():
        out.append(ContentItem(label=DocItemLabel.TEXT, level=buf_level, text=buf, page=buf_page))
    return out


def _prov_page_bbox(node: Any) -> tuple[Optional[int], Optional[tuple[float, float, float, float]]]:
    """issue 0032: a docling item's page (1-based) and best-effort bbox from `prov[0]` (the same provenance
    `scan_quality._page_texts` reads). Returns `(None, None)` when the item has no provenance (a test stub or a
    born-item with none). The bbox is `(l, t, r, b)` when the parser produced one, else `None`."""
    prov = getattr(node, "prov", None) or []
    if not prov:
        return None, None
    page = getattr(prov[0], "page_no", None)
    box = getattr(prov[0], "bbox", None)
    bbox = None
    if box is not None:
        try:
            bbox = (float(box.l), float(box.t), float(box.r), float(box.b))
        except (AttributeError, TypeError, ValueError):
            bbox = None
    return (int(page) if page is not None else None), bbox


def content_items(doc: Any) -> list[ContentItem]:
    """A parsed document -> its READING-ORDER chunkable content items (issue 0014). Walks `iterate_items` over the
    BODY and FURNITURE layers (so everything in `document.texts`, incl. page furniture, is covered), mapping each
    item to a `ContentItem`: a TABLE -> its atomic markdown, a PICTURE -> caption + description text, any other
    item -> its `.text`. A table/picture with no extractable text yields an empty-text item (the chunker strips it
    exactly as it strips an empty text item today) -- present in reading order, never silently missing.

    NO-SILENT-LOSS GUARANTEE (0014, the issue's Q2): any `document.texts` item the reading-order walk did not
    visit is appended, so the projection is a strict SUPERSET of `.texts` -- a content item can never vanish
    upstream of the failure accounting the way a table did before this fix."""
    from docling_core.types.doc.document import ContentLayer

    def _text_item(node: Any) -> ContentItem:
        page, bbox = _prov_page_bbox(node)
        return ContentItem(label=getattr(node, "label", None), level=getattr(node, "level", None),
                           text=getattr(node, "text", "") or "", page=page, bbox=bbox)

    if not hasattr(doc, "iterate_items"):  # a plain `.texts`-bearing view (a `_SubDocument` slice / test stub):
        return [_text_item(t) for t in getattr(doc, "texts", None) or []]  # no reading-order body, no tables

    layers = {ContentLayer.BODY, ContentLayer.FURNITURE}
    items: list[ContentItem] = []
    seen: set[int] = set()
    for node, _level in doc.iterate_items(included_content_layers=layers):
        seen.add(id(node))
        label = getattr(node, "label", None)
        if label == DocItemLabel.TABLE and hasattr(node, "export_to_markdown"):
            text = _table_content_text(node, doc)
        elif label == DocItemLabel.PICTURE:
            text = _picture_content_text(node, doc)
        else:
            text = getattr(node, "text", "") or ""
        page, bbox = _prov_page_bbox(node)
        items.append(ContentItem(label=label, level=getattr(node, "level", None), text=text,
                                 page=page, bbox=bbox))
    items = _merge_wrapped_lines(items)  # DEFRAG-1: rejoin per-line items into whole-clause paragraphs
    for text_item in getattr(doc, "texts", None) or []:  # coverage backstop: never drop a `.texts` item
        if id(text_item) not in seen:
            items.append(_text_item(text_item))
    return items


def _section_number(heading: str, index: int) -> str:
    """A short section id: the leading numeric token of the heading (e.g. '1' from '1. Confidentiality'), else the
    1-based position -- so the compliance citation is stable and human-meaningful."""
    token = heading.strip().split()[0].rstrip(".").rstrip(")") if heading.strip() else ""
    return token if token and any(c.isdigit() for c in token) else str(index)


def document_to_sections(doc: Any) -> list[dict]:
    """A parsed document -> `[{section, heading, text}]` split at its headings -- the compliance side's shape
    (`RegulationAdapter` ingests exactly this). Body before the first heading is kept as a leading section (heading
    ""), so nothing is dropped. PAGE_HEADER and whitespace-only items are skipped."""
    sections: list[dict] = []
    heading = ""
    body: list[str] = []

    def _flush() -> None:
        text = "\n".join(body).strip()
        if heading or text:  # keep a section if it has a heading OR any body (never emit a fully empty one)
            sections.append({"section": _section_number(heading, len(sections) + 1), "heading": heading, "text": text})

    for item, _level in doc.iterate_items():
        label = getattr(item, "label", None)
        text = (getattr(item, "text", "") or "").strip()
        if label in _HEADING_LABELS:
            _flush()  # close the previous section
            heading = text
            body = []
        elif label == DocItemLabel.PAGE_HEADER:
            continue  # running page furniture -> neither a boundary nor body
        elif text:
            body.append(text)
    _flush()  # the final section
    return sections
