"""Issue 0009-WIRE: the tiered OCR parser -- fast OCR -> scan-quality gate -> VLM escalation for degraded pages
-> PARTIAL (unreadable_pages) for what even the VLM can't read. A `Parser` (convert(source)->DoclingDocument),
so it drops into the existing `parse(..., parser=)` seam. Hermetic: fake fast/VLM parsers, no docling, no API."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from rag_wright.capabilities.parsing import TieredOCRParser

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


def test_graceful_degrade_when_vlm_errors():
    class _BoomVlm:
        def convert(self, source):
            raise RuntimeError("openrouter 500")

    fast = _FakeParser(_Doc([_item(_GARBAGE, 1)]))
    p = TieredOCRParser(fast=fast, vlm=_BoomVlm())
    doc = p.convert(Path("x.pdf"))
    assert doc is fast.doc and p.report.unreadable_pages == [1]  # VLM failed -> PARTIAL, no crash
