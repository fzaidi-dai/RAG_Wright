"""ING-6 test helpers: build synthetic Office Open XML packages with embedded files (no client data).

`make_cfb` writes a minimal OLE Compound File (v3, 512-byte sectors, regular-sector streams only, so every stream
must be >= 4096 bytes), enough for `olefile` to read back. `ole10native` packs a file the way Windows Packager does.
"""
from __future__ import annotations

import io
import struct
import zipfile
from pathlib import Path

_END, _FREE, _FATSECT, _NOSTREAM = 0xFFFFFFFE, 0xFFFFFFFF, 0xFFFFFFFD, 0xFFFFFFFF


def make_cfb(streams: dict[str, bytes]) -> bytes:
    assert 1 <= len(streams) <= 3 and all(len(d) >= 4096 for d in streams.values())
    chunks = [d + b"\0" * (-len(d) % 512) for d in streams.values()]
    starts, sector = [], 2  # sector 0 = FAT, sector 1 = directory
    for c in chunks:
        starts.append(sector)
        sector += len(c) // 512
    fat = [_FATSECT, _END]
    for st, c in zip(starts, chunks):
        n = len(c) // 512
        fat += [st + k + 1 for k in range(n - 1)] + [_END]
    assert len(fat) <= 128
    fat += [_FREE] * (128 - len(fat))

    def entry(name, typ, left=_NOSTREAM, right=_NOSTREAM, child=_NOSTREAM, start=0, size=0):
        raw = name.encode("utf-16-le")
        nm = raw + b"\0\0" if name else b""
        return (nm.ljust(64, b"\0") + struct.pack("<HBB", len(nm), typ, 1) + struct.pack("<III", left, right, child)
                + b"\0" * 16 + b"\0" * 4 + b"\0" * 16 + struct.pack("<III", start, size, 0))

    names = list(streams)
    dirs = [entry("Root Entry", 5, child=1, start=_END)]
    for i, (name, st) in enumerate(zip(names, starts), 1):
        dirs.append(entry(name, 2, right=i + 1 if i < len(names) else _NOSTREAM, start=st, size=len(streams[name])))
    while len(dirs) < 4:
        dirs.append(entry("", 0))
    header = (bytes.fromhex("D0CF11E0A1B11AE1") + b"\0" * 16 + struct.pack("<HHHHH", 0x3E, 3, 0xFFFE, 9, 6)
              + b"\0" * 6 + struct.pack("<IIIIIIIII", 0, 1, 1, 0, 4096, _END, 0, _END, 0)
              + struct.pack("<I", 0) + struct.pack("<I", _FREE) * 108)
    return header + struct.pack("<128I", *fat) + b"".join(dirs) + b"".join(chunks)


def ole10native(filename: str, data: bytes, src_path: str = "C:\\Users\\someone\\Desktop\\private\\") -> bytes:
    label = filename.encode("latin-1") + b"\0"
    path = (src_path + filename).encode("latin-1") + b"\0"
    temp = (src_path + "tmp\\" + filename).encode("latin-1") + b"\0"
    body = (struct.pack("<H", 2) + label + path + struct.pack("<HH", 0, 3) + struct.pack("<I", len(temp)) + temp
            + struct.pack("<I", len(data)) + data + b"\0" * 32)
    return struct.pack("<I", len(body)) + body


def fake_pdf(tag: str) -> bytes:
    body = f"%PDF-1.4\n% synthetic test report {tag}\n".encode() + b"0" * 5000
    return body + b"\n%%EOF\n"


def packager(filename: str, data: bytes) -> bytes:
    return make_cfb({"\x01Ole10Native": ole10native(filename, data)})


_NS_MAIN = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
_NS_R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
_REL_OLE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject"
_REL_PKG = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/package"


def make_xlsx(path: Path, rows: list[list], embeds: list[tuple[int, int, str, bytes]], *, sheet="Results") -> Path:
    """A one-sheet workbook with `rows`, plus embedded objects `(col, row, part_name, part_bytes)` anchored at the
    0-based cell. A part ending in `.bin` is an OLE object; any other part is an embedded package (e.g. `.docx`)."""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    src = zipfile.ZipFile(io.BytesIO(buf.getvalue()))
    rels = [f'<Relationship Id="rIdE{i}" Type="{_REL_OLE if part.endswith(".bin") else _REL_PKG}" '
            f'Target="../embeddings/{part}"/>' for i, (_c, _r, part, _b) in enumerate(embeds)]
    objs = "".join(
        f'<mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">'
        f'<mc:Choice Requires="x14"><oleObject progId="Package" shapeId="{1025 + i}" r:id="rIdE{i}">'
        f'<objectPr defaultSize="0"><anchor moveWithCells="1"><from><xdr:col>{c}</xdr:col><xdr:colOff>0</xdr:colOff>'
        f'<xdr:row>{r}</xdr:row><xdr:rowOff>0</xdr:rowOff></from><to><xdr:col>{c}</xdr:col><xdr:colOff>9</xdr:colOff>'
        f'<xdr:row>{r + 1}</xdr:row><xdr:rowOff>9</xdr:rowOff></to></anchor></objectPr></oleObject></mc:Choice>'
        f'<mc:Fallback><oleObject progId="Package" shapeId="{1025 + i}" r:id="rIdE{i}"/></mc:Fallback>'
        f'</mc:AlternateContent>' for i, (c, r, _p, _b) in enumerate(embeds))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml" and embeds:
                text = data.decode()
                text = text.replace("<worksheet ", '<worksheet xmlns:xdr="http://schemas.openxmlformats.org/'
                                    'drawingml/2006/spreadsheetDrawing" ', 1)
                if _NS_R not in text:
                    text = text.replace(_NS_MAIN, f"{_NS_MAIN} {_NS_R}", 1)
                data = text.replace("</worksheet>", f"<oleObjects>{objs}</oleObjects></worksheet>").encode()
            if item.filename == "[Content_Types].xml":
                data = data.replace(b"<Default ", b'<Default Extension="bin" ContentType="application/'
                                    b'vnd.openxmlformats-officedocument.oleObject"/><Default ', 1)
            dst.writestr(item, data)
        if embeds:
            dst.writestr("xl/worksheets/_rels/sheet1.xml.rels",
                         '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.'
                         f'org/package/2006/relationships">{"".join(rels)}</Relationships>')
            for _c, _r, part, blob in embeds:
                dst.writestr(f"xl/embeddings/{part}", blob)
    path.write_bytes(out.getvalue())
    return path


def make_docx(path: Path, paragraphs: list[str], embeds: dict[int, tuple[str, bytes]]) -> Path:
    """A minimal .docx: one paragraph per string; `embeds` maps a 0-based paragraph index to (part, bytes)."""
    w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    o = 'xmlns:o="urn:schemas-microsoft-com:office:office"'
    paras = []
    for i, text in enumerate(paragraphs):
        obj = (f'<w:r><w:object><o:OLEObject Type="Embed" ProgID="Package" ShapeID="_x{i}" r:id="rIdE{i}"/>'
               f'</w:object></w:r>') if i in embeds else ""
        paras.append(f"<w:p><w:r><w:t>{text}</w:t></w:r>{obj}</w:p>")
    doc = (f'<?xml version="1.0" encoding="UTF-8"?><w:document {w} {o} {_NS_R}><w:body>{"".join(paras)}'
           f'</w:body></w:document>')
    rels = "".join(f'<Relationship Id="rIdE{i}" Type="{_REL_OLE if part.endswith(".bin") else _REL_PKG}" '
                   f'Target="embeddings/{part}"/>' for i, (part, _b) in embeds.items())
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/'
                   '2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Default Extension='
                   '"rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension'
                   '="bin" ContentType="application/vnd.openxmlformats-officedocument.oleObject"/><Override PartName='
                   '"/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.'
                   'document.main+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.'
                   'openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.'
                   'openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                   '</Relationships>')
        z.writestr("word/document.xml", doc)
        z.writestr("word/_rels/document.xml.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="'
                   f'http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>')
        for part, blob in embeds.values():
            z.writestr(f"word/embeddings/{part}", blob)
    return path
