"""Issue 0009-WIRE: the tiered OCR parser -- fast OCR -> scan-quality gate -> VLM escalation for degraded pages
-> PARTIAL (unreadable_pages) for what even the VLM can't read. A `Parser` (convert(source)->DoclingDocument),
so it drops into the existing `parse(..., parser=)` seam. Hermetic: fake fast/VLM parsers, no docling, no API."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from rag_wright.capabilities.parsing import TieredOCRParser, _text_layer_pages


def _minimal_pdf(text: str) -> bytes:
    """A valid single-page PDF carrying `text` as a real (Helvetica) text layer, with correct xref offsets --
    so `_text_layer_pages` (pypdfium2, no OCR) reads it back. Lets the threshold be tested without a PDF-gen lib."""
    esc = text.replace("\\", "\\\\").replace("(", r"\(").replace(")", r"\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({esc}) Tj ET".encode("latin-1")
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length %d>>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (i, obj)
    xref_pos = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref_pos)
    return bytes(out)

_REAL = ("shall not exceed the total fees paid by the Customer under this Agreement in the twelve months "
         "preceding the claim, and nothing in this clause limits either party for death or personal injury")
_GARBAGE = "upareaia aes jo sa ayo worse eas ss Pesioperann dae esp Al sone stm JO TPE cer ecm snd women pe"


def _item(text, page):
    return SimpleNamespace(text=text, prov=[SimpleNamespace(page_no=page)])


class _Doc:
    def __init__(self, items):
        self.items = items

    def iterate_items(self):
        return [(i, 0) for i in self.items]


class _FakeParser:
    def __init__(self, doc):
        self.doc = doc
        self.calls = 0

    def convert(self, source):
        self.calls += 1
        return self.doc


def test_readable_doc_is_not_escalated():
    fast = _FakeParser(_Doc([_item(_REAL, 1), _item(_REAL, 2)]))
    vlm = _FakeParser(_Doc([_item(_REAL, 1)]))
    p = TieredOCRParser(fast=fast, vlm=vlm)
    doc = p.convert(Path("x.pdf"))
    assert doc is fast.doc and vlm.calls == 0                     # VLM never called on a readable doc
    assert p.report.escalated_pages == [] and p.report.unreadable_pages == []


def test_degraded_page_escalates_to_vlm_and_the_vlm_reads_it():
    fast = _FakeParser(_Doc([_item(_GARBAGE, 1), _item(_REAL, 2)]))   # page 1 is garbage OCR
    vlm = _FakeParser(_Doc([_item(_REAL, 1), _item(_REAL, 2)]))       # VLM reads it
    p = TieredOCRParser(fast=fast, vlm=vlm)
    doc = p.convert(Path("x.pdf"))
    assert doc is vlm.doc and vlm.calls == 1                          # escalated to the VLM doc
    assert p.report.escalated_pages == [1] and p.report.unreadable_pages == []


def test_unreadable_after_vlm_is_flagged_partial():
    fast = _FakeParser(_Doc([_item(_GARBAGE, 1)]))
    vlm = _FakeParser(_Doc([_item(_GARBAGE, 1)]))                     # VLM ALSO fails -> genuine info loss
    p = TieredOCRParser(fast=fast, vlm=vlm)
    p.convert(Path("x.pdf"))
    assert p.report.escalated_pages == [1] and p.report.unreadable_pages == [1]


# --- 0009-WIRE2: graceful degrade (default-on safety) -------------------------------------------

def test_graceful_degrade_when_no_vlm_configured(monkeypatch):
    # default-on: a degraded page with NO VLM key must NOT crash -- return the fast doc, flag PARTIAL
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    fast = _FakeParser(_Doc([_item(_GARBAGE, 1)]))
    p = TieredOCRParser(fast=fast)  # no vlm injected + no key -> cannot escalate
    doc = p.convert(Path("x.pdf"))
    assert doc is fast.doc                                       # no crash; keep the fast doc
    assert p.report.escalated_pages == [] and p.report.unreadable_pages == [1]  # flagged PARTIAL


# --- PARSE-1: text-layer-first (a born-digital page is authoritative; never OCR-escalate it) -----

def test_born_digital_page_is_not_escalated_even_when_the_ocr_gate_flags_it(monkeypatch):
    # PARSE-1 (NEONSYSTEMS): page 1 is a born-digital signature page -- sparse prose trips the OCR word-hit gate
    # ("degraded"), but it has an authoritative PDF TEXT LAYER, so it must NOT trigger a (whole-document) VLM
    # escalation. This is the 4-min false-positive on real contracts.
    import rag_wright.capabilities.parsing as parsing
    monkeypatch.setattr(parsing, "_text_layer_pages", lambda source: {1})  # page 1 is born-digital
    fast = _FakeParser(_Doc([_item(_GARBAGE, 1)]))                          # OCR gate would flag it degraded
    vlm = _FakeParser(_Doc([_item(_REAL, 1)]))
    p = TieredOCRParser(fast=fast, vlm=vlm)
    doc = p.convert(Path("x.pdf"))
    assert doc is fast.doc and vlm.calls == 0                               # NO VLM escalation: text layer wins
    assert p.report.escalated_pages == [] and p.report.unreadable_pages == []


def test_scanned_page_without_text_layer_still_escalates(monkeypatch):
    # contrast: a genuine image-only page (no text layer) that is degraded MUST still escalate (unchanged).
    import rag_wright.capabilities.parsing as parsing
    monkeypatch.setattr(parsing, "_text_layer_pages", lambda source: set())  # no born-digital pages
    fast = _FakeParser(_Doc([_item(_GARBAGE, 1)]))
    vlm = _FakeParser(_Doc([_item(_REAL, 1)]))
    p = TieredOCRParser(fast=fast, vlm=vlm)
    doc = p.convert(Path("x.pdf"))
    assert doc is vlm.doc and vlm.calls == 1                                # still escalates a true scan
    assert p.report.escalated_pages == [1]


def test_text_layer_pages_recognizes_a_sparse_born_digital_page(tmp_path):
    # PARSE-2 (doc3): a sparse-but-real born-digital page (a schedule / signature page, ~100 chars) MUST count as
    # born-digital at the default threshold, so it is never VLM-escalated. doc3 pages 52-58 (91-179 chars) fell
    # below the old 200 threshold, so one gate false-positive triggered a whole-doc VLM escalation -> 600s deadline.
    sparse = tmp_path / "sparse.pdf"
    sparse.write_bytes(_minimal_pdf("Schedule A. The parties have executed this Agreement as of the date first "
                                    "written above by their duly authorized representatives."))
    assert _text_layer_pages(sparse) == {1}                     # protected at the (low) default threshold
    assert _text_layer_pages(sparse, min_chars=1000) == set()   # min_chars parameter mechanics


def test_text_layer_pages_excludes_a_near_empty_image_page(tmp_path):
    # a true image-only page extracts ~0 chars -> NOT born-digital -> still routed through OCR/VLM (unchanged).
    img = tmp_path / "img.pdf"
    img.write_bytes(_minimal_pdf("9"))
    assert _text_layer_pages(img) == set()


def test_mixed_pdf_escalates_only_the_imageless_page(monkeypatch):
    # a mixed PDF: page 1 born-digital (sparse -> gate flags it), page 2 a true scanned image (no text layer).
    # Only page 2 escalates; page 1's text layer is authoritative.
    import rag_wright.capabilities.parsing as parsing
    monkeypatch.setattr(parsing, "_text_layer_pages", lambda source: {1})   # only page 1 is born-digital
    fast = _FakeParser(_Doc([_item(_GARBAGE, 1), _item(_GARBAGE, 2)]))      # both flagged by the OCR gate
    vlm = _FakeParser(_Doc([_item(_GARBAGE, 1), _item(_REAL, 2)]))          # VLM reads page 2
    p = TieredOCRParser(fast=fast, vlm=vlm)
    p.convert(Path("x.pdf"))
    assert vlm.calls == 1 and p.report.escalated_pages == [2]               # page 1 (text layer) NOT escalated


def test_graceful_degrade_when_vlm_errors():
    class _BoomVlm:
        def convert(self, source):
            raise RuntimeError("openrouter 500")

    fast = _FakeParser(_Doc([_item(_GARBAGE, 1)]))
    p = TieredOCRParser(fast=fast, vlm=_BoomVlm())
    doc = p.convert(Path("x.pdf"))
    assert doc is fast.doc and p.report.unreadable_pages == [1]  # VLM failed -> PARTIAL, no crash
