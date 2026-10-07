"""ING-6/6b (ADR-0124): extract files embedded in Office Open XML packages -- and files ATTACHED to a PDF (ING-6b,
no anchor: a parent-document link only) -- so they become CHILD documents.

Generic over spreadsheets, word-processing documents and presentations (`.xlsx`/`.xlsm`/`.docx`/`.docm`/`.pptx`/
`.pptm`): every part under `*/embeddings/` is one embedded object. An OLE object is unwrapped to its payload -- a
Windows Packager object (`\\x01Ole10Native`) yields the original file and its display NAME (the sender's local
path stored beside it is discarded), an OLE-wrapped PDF or Office file yields its `CONTENTS`/`Package` stream -- and
an embedded package part (e.g. an embedded `.docx`) is taken as-is. Each file carries where it sits in the parent:
sheet + cell (+ 0-based row/col) for a spreadsheet, paragraph index for a document, slide number for a deck.
Identical files are returned once with all their anchors; an object that cannot be extracted is listed in `skipped`
with the reason. Docling ignores embedded objects, so without this they would be lost silently.
"""
from __future__ import annotations

import hashlib
import io
import posixpath
import re
import struct
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import PureWindowsPath
from typing import Optional

_PACKAGE_EXTS = frozenset({"xlsx", "xlsm", "docx", "docm", "pptx", "pptm"})
_EMBEDDED_PART = re.compile(r"^(?:xl|word|ppt)/embeddings/[^/]+$")
_R_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
_OOXML_MEDIA = {
    "word/": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xl/": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ppt/": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


@dataclass(frozen=True)
class EmbeddedAnchor:
    """Where an embedded object sits in its parent (fields not applicable to the format are None)."""

    sheet: Optional[str] = None
    cell: Optional[str] = None  # e.g. "R2"
    row: Optional[int] = None   # 0-based sheet row
    col: Optional[int] = None   # 0-based sheet column
    paragraph: Optional[int] = None  # 0-based paragraph index (word-processing)
    slide: Optional[int] = None      # 1-based slide number (presentation)


@dataclass
class EmbeddedFile:
    data: bytes
    filename: Optional[str]
    media_type: str
    sha256: str
    anchors: list[EmbeddedAnchor] = field(default_factory=list)


@dataclass
class EmbeddedExtraction:
    files: list[EmbeddedFile] = field(default_factory=list)
    found: int = 0       # embedded objects in the package
    duplicates: int = 0  # objects whose file was already extracted (merged as an extra anchor)
    skipped: list[str] = field(default_factory=list)  # "<part>: <reason>" for objects that could not be extracted


def parse_ole10native(raw: bytes) -> tuple[str, bytes]:
    """A Windows Packager `\\x01Ole10Native` stream -> (display file name, file bytes). Layout: size(u32),
    flags(u16), name\\0, source path\\0 (discarded: it is the sender's local path), u16, u16, temp-path length(u32),
    temp path, data length(u32), data."""
    i = 6
    end = raw.index(b"\0", i)
    name = raw[i:end].decode("cp1252", errors="replace")
    i = raw.index(b"\0", end + 1) + 1 + 4  # skip the source path and the two u16 fields
    (temp_len,) = struct.unpack_from("<I", raw, i)
    i += 4 + temp_len
    (size,) = struct.unpack_from("<I", raw, i)
    data = raw[i + 4:i + 4 + size]
    if len(data) != size:
        raise ValueError(f"truncated Ole10Native payload ({len(data)} of {size} bytes)")
    return PureWindowsPath(name).name, data


def _media_type(data: bytes) -> str:
    if data.startswith(b"%PDF"):
        return "application/pdf"
    if data.startswith(b"PK\x03\x04"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
        return next((m for prefix, m in _OOXML_MEDIA.items() if any(n.startswith(prefix) for n in names)),
                    "application/zip")
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
        return "application/x-ole-storage"  # a legacy binary Office file (.doc/.xls/.ppt)
    return "application/octet-stream"


def _unwrap(part: str, blob: bytes) -> tuple[bytes, Optional[str]]:
    """An embedded part -> (file bytes, original file name if known). Raises ValueError when nothing extractable."""
    import olefile

    if not olefile.isOleFile(data=blob):
        return blob, posixpath.basename(part)  # an embedded package part: the file itself
    with olefile.OleFileIO(blob) as ole:
        if ole.exists("\x01Ole10Native"):
            name, data = parse_ole10native(ole.openstream("\x01Ole10Native").read())
            return data, name
        for stream in ("CONTENTS", "Package"):  # an OLE-wrapped PDF / Office file
            if ole.exists(stream):
                return ole.openstream(stream).read(), None
        streams = ["/".join(s) for s in ole.listdir()]
    raise ValueError(f"OLE object with no extractable payload (streams: {streams})")


def _rels(z: zipfile.ZipFile, part: str) -> dict[str, str]:
    """A part's relationships: id -> absolute target part name."""
    base, name = posixpath.split(part)
    rels_part = posixpath.join(base, "_rels", f"{name}.rels")
    if rels_part not in z.namelist():
        return {}
    out = {}
    for rel in ET.fromstring(z.read(rels_part)):
        target = rel.get("Target", "")
        out[rel.get("Id")] = posixpath.normpath(posixpath.join(base, target)) if not target.startswith("/") \
            else target.lstrip("/")
    return out


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _column_letters(col: int) -> str:
    letters = ""
    col += 1
    while col:
        col, rem = divmod(col - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _spreadsheet_anchors(z: zipfile.ZipFile) -> dict[str, list[EmbeddedAnchor]]:
    out: dict[str, list[EmbeddedAnchor]] = {}
    wb_rels = _rels(z, "xl/workbook.xml")
    for sheet in ET.fromstring(z.read("xl/workbook.xml")).iter():
        if _local(sheet.tag) != "sheet":
            continue
        sheet_part = wb_rels.get(sheet.get(_R_ID))
        if not sheet_part or sheet_part not in z.namelist():
            continue
        targets = _rels(z, sheet_part)
        seen: set[str] = set()  # an object appears twice (mc:Choice with its anchor first, then mc:Fallback)
        for obj in ET.fromstring(z.read(sheet_part)).iter():
            if _local(obj.tag) != "oleObject" or obj.get(_R_ID) in seen:
                continue
            seen.add(obj.get(_R_ID))
            target = targets.get(obj.get(_R_ID))
            if not target:
                continue
            frm = next((e for e in obj.iter() if _local(e.tag) == "from"), None)
            pos = {_local(e.tag): int(e.text) for e in (frm if frm is not None else []) if _local(e.tag) in ("col", "row")}
            col, row = pos.get("col"), pos.get("row")
            cell = f"{_column_letters(col)}{row + 1}" if col is not None and row is not None else None
            out.setdefault(target, []).append(EmbeddedAnchor(sheet=sheet.get("name"), cell=cell, row=row, col=col))
    return out


def _document_anchors(z: zipfile.ZipFile) -> dict[str, list[EmbeddedAnchor]]:
    out: dict[str, list[EmbeddedAnchor]] = {}
    targets = _rels(z, "word/document.xml")
    paragraphs = [e for e in ET.fromstring(z.read("word/document.xml")).iter() if _local(e.tag) == "p"]
    for index, p in enumerate(paragraphs):
        for e in p.iter():
            target = targets.get(e.get(_R_ID))
            if target and _EMBEDDED_PART.match(target):
                out.setdefault(target, []).append(EmbeddedAnchor(paragraph=index))
    return out


def _presentation_anchors(z: zipfile.ZipFile) -> dict[str, list[EmbeddedAnchor]]:
    out: dict[str, list[EmbeddedAnchor]] = {}
    pres_rels = _rels(z, "ppt/presentation.xml")
    slide_ids = [e.get(_R_ID) for e in ET.fromstring(z.read("ppt/presentation.xml")).iter()
                 if _local(e.tag) == "sldId"]
    for number, rid in enumerate(slide_ids, 1):
        for target in _rels(z, pres_rels.get(rid, "")).values():
            if _EMBEDDED_PART.match(target):
                out.setdefault(target, []).append(EmbeddedAnchor(slide=number))
    return out


def _anchors(z: zipfile.ZipFile) -> dict[str, list[EmbeddedAnchor]]:
    names = set(z.namelist())
    if "xl/workbook.xml" in names:
        return _spreadsheet_anchors(z)
    if "word/document.xml" in names:
        return _document_anchors(z)
    if "ppt/presentation.xml" in names:
        return _presentation_anchors(z)
    return {}


def _natural(part: str) -> list:
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", part)]


def _is_empty_flate(payload: bytes) -> bool:
    """pdfium returns an EMPTY attachment as the raw FlateDecode encoding of nothing (8 bytes) instead of b''."""
    import zlib

    if len(payload) > 16:
        return False
    try:
        return zlib.decompress(payload) == b""
    except zlib.error:
        return False


def _pdf_attachments(data: bytes) -> EmbeddedExtraction:
    """ING-6b: the files attached to a PDF (its embedded-files name tree). An attachment has no position in the
    page content, so it carries no anchor -- the child links to the parent document only."""
    import pypdfium2

    out = EmbeddedExtraction()
    by_hash: dict[str, EmbeddedFile] = {}
    try:
        pdf = pypdfium2.PdfDocument(data)
    except Exception as exc:  # noqa: BLE001 - an unreadable PDF has no attachments we can list; say so
        out.skipped.append(f"attachments: {type(exc).__name__}: {exc}")
        return out
    try:
        for i in range(pdf.count_attachments()):
            out.found += 1
            name = f"attachment {i}"
            try:
                att = pdf.get_attachment(i)
                name = att.get_name() or name
                payload = bytes(att.get_data())
            except Exception as exc:  # noqa: BLE001 - one unreadable attachment is reported, the rest extracted
                out.skipped.append(f"{name}: {type(exc).__name__}: {exc}")
                continue
            if not payload or _is_empty_flate(payload):
                out.skipped.append(f"{name}: empty attachment")
                continue
            sha = hashlib.sha256(payload).hexdigest()
            if sha in by_hash:
                out.duplicates += 1
                continue
            by_hash[sha] = EmbeddedFile(data=payload, filename=PureWindowsPath(name).name,
                                        media_type=_media_type(payload), sha256=sha)
    finally:
        pdf.close()
    out.files = list(by_hash.values())
    return out


def extract_embedded(name: str, data: bytes) -> EmbeddedExtraction:
    """The files embedded in an Office Open XML package, or attached to a PDF (empty for any other source)."""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext == "pdf" or data.startswith(b"%PDF"):
        return _pdf_attachments(data)
    if ext not in _PACKAGE_EXTS or not zipfile.is_zipfile(io.BytesIO(data)):
        return EmbeddedExtraction()
    out = EmbeddedExtraction()
    by_hash: dict[str, EmbeddedFile] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        anchors = _anchors(z)
        for part in sorted((n for n in z.namelist() if _EMBEDDED_PART.match(n)), key=_natural):
            out.found += 1
            try:
                payload, filename = _unwrap(part, z.read(part))
            except (ValueError, OSError, struct.error) as exc:
                out.skipped.append(f"{part}: {exc}")
                continue
            sha = hashlib.sha256(payload).hexdigest()
            if sha in by_hash:
                out.duplicates += 1
                by_hash[sha].anchors.extend(anchors.get(part, []))
                continue
            by_hash[sha] = EmbeddedFile(data=payload, filename=filename, media_type=_media_type(payload), sha256=sha,
                                        anchors=list(anchors.get(part, [])))
    out.files = list(by_hash.values())
    return out


# --- ING-6 record links: which record (table row) an embedded file belongs to -----------------------------------

_ALNUM = re.compile(r"[a-z0-9]+")


def candidate_tokens(text: str) -> set[str]:
    """Identifier-shaped tokens of `text`: 4+ letters/digits with at least one digit (case-folded)."""
    return {t for t in _ALNUM.findall(text.lower()) if len(t) >= 4 and any(c.isdigit() for c in t)}


def file_text(data: bytes, media_type: str) -> str:
    """The text of an embedded file, for link evidence (a PDF's text layer; empty when unreadable or another type)."""
    if media_type != "application/pdf":
        return ""
    try:
        import pypdfium2

        pdf = pypdfium2.PdfDocument(data)
        try:
            return " ".join(pdf[i].get_textpage().get_text_range() for i in range(len(pdf)))
        finally:
            pdf.close()
    except Exception:  # noqa: BLE001 - an unreadable PDF is still extracted; it just offers no link evidence
        return ""
