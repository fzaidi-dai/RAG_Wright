"""ING-6 (ADR-0124): files embedded in Office Open XML packages become CHILD documents.

Generic over xlsx/xlsm/docx/pptx: OLE Packager objects are unwrapped to the original bytes + filename (never the
sender's local path), embedded packages are taken as-is, each file carries its anchor (sheet + cell + row, or
paragraph / slide), identical files are stored once with all anchors, and anything that cannot be extracted is
reported, never silently dropped. Hermetic: synthetic packages built in `_ooxml_fixtures`."""
from __future__ import annotations

import hashlib
import io
import zipfile

from rag_wright.corpus.embedded import extract_embedded, parse_ole10native

from tests.corpus._ooxml_fixtures import fake_pdf, make_cfb, make_docx, make_xlsx, ole10native, packager

_ROWS = [["Sr#", "Sample", "Standard", "Report"], [1, "SDP 3101", "EN 17092", "R-1"],
         [2, "SDP 3102", "EN 388", "R-2"], [3, "SDP 3103", "EN 17092", "R-1"]]


def test_ole10native_yields_the_file_and_its_name_but_not_the_senders_path():
    name, data = parse_ole10native(ole10native("report A.pdf", b"%PDF-1.4 body", "C:\\Users\\jane\\Desktop\\"))
    assert name == "report A.pdf" and data == b"%PDF-1.4 body"


def test_an_xlsx_ole_package_is_unwrapped_with_its_cell_anchor(tmp_path):
    x = make_xlsx(tmp_path / "db.xlsx", _ROWS, [(3, 1, "oleObject1.bin", packager("R-1 lab report.pdf", fake_pdf("1")))])
    out = extract_embedded(x.name, x.read_bytes())
    (f,) = out.files
    assert f.filename == "R-1 lab report.pdf" and f.media_type == "application/pdf"
    assert f.data == fake_pdf("1") and f.sha256 == hashlib.sha256(fake_pdf("1")).hexdigest()
    (a,) = f.anchors
    assert (a.sheet, a.cell, a.row, a.col) == ("Results", "D2", 1, 3)
    assert out.found == 1 and out.skipped == []


def test_the_same_file_embedded_twice_is_one_child_with_two_anchors(tmp_path):
    blob = packager("R-1 lab report.pdf", fake_pdf("1"))
    x = make_xlsx(tmp_path / "db.xlsx", _ROWS, [(3, 1, "oleObject1.bin", blob), (3, 3, "oleObject2.bin", blob)])
    out = extract_embedded(x.name, x.read_bytes())
    assert len(out.files) == 1 and [a.cell for a in out.files[0].anchors] == ["D2", "D4"]
    assert out.found == 2 and out.duplicates == 1


def _docx_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", "<w:document/>")
    return buf.getvalue()


def test_an_embedded_office_package_is_taken_as_is(tmp_path):
    x = make_xlsx(tmp_path / "db.xlsx", _ROWS, [(3, 2, "Microsoft_Word_Document.docx", _docx_bytes())])
    (f,) = extract_embedded(x.name, x.read_bytes()).files
    assert f.media_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert f.filename == "Microsoft_Word_Document.docx" and f.anchors[0].cell == "D3"


def test_an_unextractable_object_is_reported_not_dropped(tmp_path):
    blob = make_cfb({"Mystery": b"\x00" * 5000})  # an OLE object with no payload stream we know
    x = make_xlsx(tmp_path / "db.xlsx", _ROWS, [(3, 1, "oleObject1.bin", blob)])
    out = extract_embedded(x.name, x.read_bytes())
    assert out.files == [] and out.found == 1
    assert len(out.skipped) == 1 and "oleObject1.bin" in out.skipped[0]


def test_a_docx_embed_is_anchored_to_its_paragraph(tmp_path):
    d = make_docx(tmp_path / "memo.docx", ["Intro", "See the report", "End"],
                  {1: ("oleObject1.bin", packager("TR-0398.pdf", fake_pdf("t")))})
    (f,) = extract_embedded(d.name, d.read_bytes()).files
    assert f.filename == "TR-0398.pdf" and f.anchors[0].paragraph == 1 and f.anchors[0].sheet is None


def test_non_package_sources_have_no_embedded_files():
    out = extract_embedded("report.pdf", fake_pdf("x"))
    assert out.files == [] and out.found == 0


# --- parse integration: children stored content-addressed beside the parse cache, linked to their table row ---

def test_parse_document_surfaces_children_linked_to_their_record_row(tmp_path):
    from rag_wright.api import parse_document

    src_dir = tmp_path / "source"
    src_dir.mkdir()
    blob1, blob2 = packager("R-1 lab report.pdf", fake_pdf("1")), packager("R-2 lab report.pdf", fake_pdf("2"))
    x = make_xlsx(src_dir / "db.xlsx", _ROWS,
                  [(3, 1, "oleObject1.bin", blob1), (3, 2, "oleObject2.bin", blob2), (3, 3, "oleObject3.bin", blob1)])
    before = sorted(p.name for p in src_dir.iterdir())
    doc = parse_document("db", x, cache_dir=tmp_path / "cache")
    assert sorted(p.name for p in src_dir.iterdir()) == before  # the source folder is never written to
    assert len(doc.embedded) == 2 and doc.embedded_skipped == []
    first = next(c for c in doc.embedded if c.filename == "R-1 lab report.pdf")
    assert first.doc_id == f"db.emb.{hashlib.sha256(fake_pdf('1')).hexdigest()[:12]}"
    assert first.path.startswith(str(tmp_path / "cache")) and open(first.path, "rb").read() == fake_pdf("1")
    assert [(a.cell, a.table_row) for a in first.anchors] == [("D2", 1), ("D4", 3)]  # data rows 1 and 3
    assert all(a.table_ref for a in first.anchors)


def test_children_are_reextracted_from_a_cached_parse(tmp_path):
    from rag_wright.api import parse_document

    x = make_xlsx(tmp_path / "db.xlsx", _ROWS, [(3, 1, "oleObject1.bin", packager("a.pdf", fake_pdf("1")))])
    parse_document("db", x, cache_dir=tmp_path / "cache")
    again = parse_document("db", x, cache_dir=tmp_path / "cache")  # parse cache hit
    assert [c.filename for c in again.embedded] == ["a.pdf"]


# --- record links: verified on the anchor row, else placed by content, else flagged -------------------------------

_DB = [["Sr#", "Sample", "Standard", "Test"],
       [1, "SDP 3101", "EN 17092", "Tear"], [1, "SDP 3101", "EN 17092", "Abrasion"],
       [2, "SDP 3102", "EN 17092", "Tear"], [2, "SDP 3102", "EN 17092", "Abrasion"],
       [3, "SDP 3103", "EN 17092", "Tear"], [3, "SDP 3103", "EN 17092", "Abrasion"]]


def _links(child):
    return sorted((lk.table_row, lk.confidence.value, lk.basis, tuple(lk.evidence)) for lk in child.links)


def _parse(tmp_path, embeds):
    from rag_wright.api import parse_document

    x = make_xlsx(tmp_path / "db.xlsx", _DB, embeds)
    return {c.filename: c for c in parse_document("db", x, cache_dir=tmp_path / "cache").embedded}


def test_a_report_on_its_own_row_is_verified_and_reaches_the_records_other_row(tmp_path):
    kids = _parse(tmp_path, [(3, 1, "oleObject1.bin", packager("SDP 3101 tear report.pdf", fake_pdf("1")))])
    assert _links(kids["SDP 3101 tear report.pdf"]) == [(1, "EXTRACTED", "anchor", ("3101",)),
                                                       (2, "INFERRED", "content", ("3101",))]


def test_a_stacked_report_is_placed_on_its_record_by_content(tmp_path):
    kids = _parse(tmp_path, [(3, 1, "oleObject1.bin", packager("SDP 3101 report.pdf", fake_pdf("1"))),
                             (3, 1, "oleObject2.bin", packager("SDP 3103 report.pdf", fake_pdf("3")))])
    assert _links(kids["SDP 3103 report.pdf"]) == [(5, "INFERRED", "content", ("3103",)),
                                                  (6, "INFERRED", "content", ("3103",))]


def test_a_stacked_report_matching_no_record_has_no_record_link(tmp_path):
    kids = _parse(tmp_path, [(3, 1, "oleObject1.bin", packager("SDP 3101 report.pdf", fake_pdf("1"))),
                             (3, 1, "oleObject2.bin", packager("SDP 9999 report.pdf", fake_pdf("9")))])
    stray = kids["SDP 9999 report.pdf"]
    assert stray.links == [] and stray.anchors[0].cell == "D2"  # still a child of the workbook, position kept


def test_a_lone_report_without_evidence_keeps_a_position_only_link(tmp_path):
    kids = _parse(tmp_path, [(3, 3, "oleObject1.bin", packager("lab report.pdf", fake_pdf("x")))])
    assert _links(kids["lab report.pdf"]) == [(3, "INFERRED", "anchor", ())]


def test_a_token_common_to_many_rows_is_not_evidence(tmp_path):
    """'17092' (the standard) is on every row: it cannot single out a record."""
    kids = _parse(tmp_path, [(3, 1, "oleObject1.bin", packager("SDP 3101 report.pdf", fake_pdf("1"))),
                             (3, 1, "oleObject2.bin", packager("EN 17092 summary.pdf", fake_pdf("s")))])
    assert kids["EN 17092 summary.pdf"].links == []


# --- ING-6b: files attached to a PDF are child documents too (no cell anchor: parent-document link only) ---------

def _pdf_with_attachments(files: list[tuple[str, bytes]]) -> bytes:
    import pypdfium2

    doc = pypdfium2.PdfDocument.new()
    doc.new_page(200, 200)
    for name, data in files:
        doc.new_attachment(name).set_data(data)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def test_pdf_attachments_are_extracted_without_an_anchor():
    pdf = _pdf_with_attachments([("results.docx", _docx_bytes()), ("raw data.pdf", fake_pdf("raw"))])
    out = extract_embedded("report.pdf", pdf)
    assert out.found == 2 and out.skipped == []
    by_name = {f.filename: f for f in out.files}
    assert set(by_name) == {"results.docx", "raw data.pdf"}
    assert by_name["raw data.pdf"].media_type == "application/pdf" and by_name["raw data.pdf"].data == fake_pdf("raw")
    assert by_name["results.docx"].media_type.endswith("wordprocessingml.document")
    assert all(f.anchors == [] for f in out.files)


def test_identical_pdf_attachments_are_one_file():
    pdf = _pdf_with_attachments([("a.pdf", fake_pdf("x")), ("copy of a.pdf", fake_pdf("x"))])
    out = extract_embedded("report.pdf", pdf)
    assert len(out.files) == 1 and out.found == 2 and out.duplicates == 1


def test_an_empty_pdf_attachment_is_reported():
    out = extract_embedded("report.pdf", _pdf_with_attachments([("empty.txt", b"")]))
    assert out.files == [] and out.found == 1 and "empty.txt" in out.skipped[0]


def test_a_pdf_without_attachments_has_none():
    assert extract_embedded("report.pdf", _pdf_with_attachments([])).found == 0


def test_parse_document_surfaces_pdf_attachments_as_children(tmp_path):
    from rag_wright.api import parse_document

    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(_pdf_with_attachments([("raw data.pdf", fake_pdf("raw"))]))
    (child,) = parse_document("rep", pdf, cache_dir=tmp_path / "cache").embedded
    assert child.doc_id == f"rep.emb.{hashlib.sha256(fake_pdf('raw')).hexdigest()[:12]}"
    assert child.filename == "raw data.pdf" and child.anchors == [] and child.links == []
