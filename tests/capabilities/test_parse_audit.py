"""PS-47: find the cached parses that hold text on a page with no ink -- what a VLM invented for a blank page
before PS-46 (TexWright 2026-10-15). The parse cache is keyed on the source's content, so the fix does not redo
those parses; this lists them so a product deletes the cache files and re-ingests. Hermetic: real tiny PDFs and
hand-written cache manifests, no docling parse, no model."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from rag_wright.capabilities.parse_audit import find_blank_pages_with_text
from tests.capabilities.test_tiered_ocr import _real_doc, _two_page_pdf_with_a_blank_second_page

_PAGE_1 = ("shall not exceed the total fees paid by the Customer under this Agreement in the twelve months "
           "preceding the claim")
_INVENTED = "Minutes of the annual meeting, attended by the board, with a summary of the year's achievements"


def _cache(cache_dir, doc_id: str, data: bytes, pages: dict[int, str], *, sidecar: bool = True) -> str:
    """Write a cached parse for `data` the way `parsed_source_document` names it."""
    h = hashlib.sha256(data).hexdigest()[:16]
    manifest = cache_dir / f"{doc_id}.{h}.json"
    _real_doc(pages).save_as_json(manifest)
    if sidecar:
        (cache_dir / f"{doc_id}.{h}.ocr.json").write_text("[2]")
    return str(manifest)


def test_a_cached_parse_with_text_on_a_blank_page_is_reported(tmp_path):
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(_two_page_pdf_with_a_blank_second_page(_PAGE_1))
    manifest = _cache(tmp_path, "report", pdf.read_bytes(), {1: _PAGE_1, 2: _INVENTED})
    [found] = find_blank_pages_with_text(tmp_path, [pdf])
    assert found.source == str(pdf)
    assert found.pages == {2: _INVENTED}
    assert found.cache_files == [manifest, manifest.replace(".json", ".ocr.json")]


def test_text_from_a_pages_own_text_layer_is_not_reported(tmp_path, monkeypatch):
    # a page that renders white but carries a text layer (white or hidden text) was read, not invented
    import rag_wright.capabilities.parsing as parsing
    monkeypatch.setattr(parsing, "_text_layer_pages", lambda source: {1, 2})
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(_two_page_pdf_with_a_blank_second_page(_PAGE_1))
    _cache(tmp_path, "report", pdf.read_bytes(), {1: _PAGE_1, 2: "hidden text layer content"})
    assert find_blank_pages_with_text(tmp_path, [pdf]) == []


def test_an_ingest_cache_is_searched_below_its_root(tmp_path):
    # `aingest(..., cache_dir=root)` keeps its parses in `root/parsed`; a product passes `root`. A same-named file
    # that is not a parse (another cache's) is skipped, never a crash.
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(_two_page_pdf_with_a_blank_second_page(_PAGE_1))
    (tmp_path / "parsed").mkdir()
    manifest = _cache(tmp_path / "parsed", "report", pdf.read_bytes(), {1: _PAGE_1, 2: _INVENTED})
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / Path(manifest).name).write_text('{"not": "a parse"}')
    [found] = find_blank_pages_with_text(tmp_path, [pdf])
    assert found.cache_files[0] == manifest


def test_a_clean_cached_parse_is_not_reported(tmp_path):
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(_two_page_pdf_with_a_blank_second_page(_PAGE_1))
    _cache(tmp_path, "report", pdf.read_bytes(), {1: _PAGE_1})
    assert find_blank_pages_with_text(tmp_path, [pdf]) == []


def test_sources_may_be_bytes_with_a_name(tmp_path):
    data = _two_page_pdf_with_a_blank_second_page(_PAGE_1)
    _cache(tmp_path, "upload", data, {1: _PAGE_1, 2: _INVENTED})
    [found] = find_blank_pages_with_text(tmp_path, [("upload.pdf", data)])
    assert found.source == "upload.pdf" and list(found.pages) == [2]


def test_embedded_files_in_the_cache_are_audited_without_being_passed(tmp_path):
    # TexWright's case: the PDFs are children of a spreadsheet. The parse stored their bytes under the parent's
    # `.embedded` folder, so the audit finds them there.
    data = _two_page_pdf_with_a_blank_second_page(_PAGE_1)
    child_dir = tmp_path / "book.0123456789abcdef.embedded"
    child_dir.mkdir()
    (child_dir / "aaaaaaaaaaaa.pdf").write_bytes(data)
    _cache(tmp_path, "book.emb.aaaaaaaaaaaa", data, {1: _PAGE_1, 2: _INVENTED}, sidecar=False)
    [found] = find_blank_pages_with_text(tmp_path)
    assert found.source == str(child_dir / "aaaaaaaaaaaa.pdf") and list(found.pages) == [2]
    assert len(found.cache_files) == 1                                      # no sidecar was written


def test_a_source_with_no_cached_parse_or_not_a_pdf_is_skipped(tmp_path):
    pdf = tmp_path / "never-parsed.pdf"
    pdf.write_bytes(_two_page_pdf_with_a_blank_second_page(_PAGE_1))
    assert find_blank_pages_with_text(tmp_path, [pdf, ("notes.md", b"# not a pdf")]) == []


def test_progress_counts_every_pdf_audited(tmp_path):
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(_two_page_pdf_with_a_blank_second_page(_PAGE_1))
    _cache(tmp_path, "report", pdf.read_bytes(), {1: _PAGE_1})
    seen = []
    find_blank_pages_with_text(tmp_path, [pdf], progress=lambda done, total: seen.append((done, total)))
    assert seen == [(1, 1)]


def test_a_missing_cache_dir_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        find_blank_pages_with_text(tmp_path / "nope")


@pytest.mark.parse
def test_a_real_ingest_cache_audit_finds_an_embedded_pdfs_invented_page(tmp_path, monkeypatch):
    # live (docling): TexWright's shape end to end. A workbook embeds a PDF whose page 2 is blank; the real
    # `build_ingestion` parses both into its cache. Page 2's text is then planted in the child's real cached parse
    # (what a pre-PS-46 VLM escalation left there). The audit of the cache root finds it with no sources passed;
    # once its `cache_files` are deleted and the workbook re-ingested, the cache is clean.
    import json

    from docling_core.types.doc.base import BoundingBox, CoordOrigin
    from docling_core.types.doc.document import DoclingDocument, ProvenanceItem
    from docling_core.types.doc.labels import DocItemLabel

    from rag_wright.api import IngestSource, find_blank_pages_with_text as public_audit
    from tests.corpus._ooxml_fixtures import make_xlsx, packager
    from tests.ingestion.test_builder import _DB, _run, _ws

    for var in ("OPENROUTER_API_KEY", "VLLM_BASE_URL", "VLLM_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    data = _two_page_pdf_with_a_blank_second_page(_PAGE_1) + b"\n" * 4096  # >= one CFB block; ignored after EOF
    book = make_xlsx(tmp_path / "db.xlsx", _DB, [(1, 1, "oleObject1.bin", packager("SDP 3101 report.pdf", data))])
    report, _ = _run(_ws(), [IngestSource(path=str(book), doc_id="db")], tmp_path)
    assert any(d.doc_id.startswith("db.emb.") and d.dead_letter is None for d in report.documents)
    cache = tmp_path / "cache"
    assert public_audit(cache) == []                                         # PS-46: nothing invented

    h = hashlib.sha256(data).hexdigest()[:16]
    [manifest] = (cache / "parsed").glob(f"db.emb.*.{h}.json")
    assert json.loads(manifest.with_name(manifest.name.replace(".json", ".ocr.json")).read_text()) == []
    doc = DoclingDocument.load_from_json(manifest)
    doc.add_text(label=DocItemLabel.TEXT, text=_INVENTED, prov=ProvenanceItem(
        page_no=2, charspan=(0, len(_INVENTED)), bbox=BoundingBox(l=0, t=0, r=1, b=1, coord_origin=CoordOrigin.TOPLEFT)))
    doc.save_as_json(manifest)

    [found] = public_audit(cache)
    assert found.pages == {2: _INVENTED} and found.source.endswith(".pdf")
    assert found.cache_files[0] == str(manifest) and found.cache_files[1].endswith(".ocr.json")
    for f in found.cache_files:
        Path(f).unlink()
    _run(_ws(), [IngestSource(path=str(book), doc_id="db")], tmp_path)
    assert public_audit(cache) == []
