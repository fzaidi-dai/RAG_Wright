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

import tempfile
from pathlib import Path
from typing import Any

from docling_core.types.doc.labels import DocItemLabel

# Labels that START a new section (a real heading), vs PAGE_HEADER which is running page furniture (ignored).
_HEADING_LABELS = frozenset({DocItemLabel.SECTION_HEADER, DocItemLabel.TITLE, DocItemLabel.FIELD_HEADING})
_TEXT_EXTS = frozenset({"txt", "md", "text"})


def parse_document_bytes(name: str, data: bytes, *, parser: Any = None) -> Any:
    """Parse raw document BYTES into a `DoclingDocument`. `.txt`/`.md` bytes are wrapped directly; binary docs
    (PDF/DOCX/HTML) go through docling's `DocumentConverter` (the existing `DoclingParser`, injectable for tests).
    `name` supplies the file extension docling needs to pick a backend."""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if parser is None:
        from rag_wright.capabilities.parsing import DoclingParser

        parser = DoclingParser()
    # docling reads a file path, not raw bytes -> write to a temp file preserving the extension for backend choice
    suffix = f".{ext}" if ext else ".txt"
    tmp = Path(tempfile.mkdtemp(prefix="docparse_")) / f"doc{suffix}"
    tmp.write_bytes(data)
    return parser.convert(tmp)


def document_to_text(doc: Any) -> str:
    """A parsed document -> one text blob (docling markdown export) -- the contract side's `parse_bytes` output."""
    return doc.export_to_markdown()


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
