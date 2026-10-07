"""0009-WIRE2: parse_document_bytes defaults to the tiered OCR parser (the one chokepoint both ingestion
pipelines + the MCP doc tool funnel through), and aparse_document_bytes runs it OFF the event loop under a
wall-clock deadline so the tiered VLM OCR can never stall the async ingestion."""
from __future__ import annotations

import time

import pytest

from rag_wright.corpus.document_parser import (
    _default_document_parser,
    aparse_document_bytes,
    parse_document_bytes,
)


def test_default_document_parser_is_the_tiered_parser():
    from rag_wright.capabilities.parsing import TieredOCRParser

    assert isinstance(_default_document_parser(), TieredOCRParser)  # chokepoint default -> tiered OCR everywhere


class _FakeParser:
    def __init__(self, doc="PARSED", delay=0.0):
        self.doc = doc
        self.delay = delay
        self.calls = 0

    def convert(self, source):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        return self.doc


def test_parse_document_bytes_uses_an_injected_parser():
    fp = _FakeParser("PARSED")
    assert parse_document_bytes("x.pdf", b"%PDF", parser=fp) == "PARSED" and fp.calls == 1


async def test_aparse_document_bytes_returns_the_parse_off_loop():
    fp = _FakeParser("PARSED")
    assert await aparse_document_bytes("x.pdf", b"%PDF", parser=fp) == "PARSED"


async def test_aparse_document_bytes_is_wall_clock_bounded():
    # a hung/slow parse must not stall the loop: the caller is unblocked at the deadline (ADR-0057)
    slow = _FakeParser("PARSED", delay=0.5)
    with pytest.raises(TimeoutError):
        await aparse_document_bytes("x.pdf", b"%PDF", parser=slow, deadline_s=0.02)


def test_a_macro_enabled_workbook_parses_as_xlsx():
    """ING-2: `.xlsm` is the same Office Open XML workbook as `.xlsx` (plus a macro part docling never runs), but
    docling only routes `.xlsx` to its spreadsheet backend -- so the bytes are handed over under `.xlsx`."""
    seen = []

    class _Rec(_FakeParser):
        def convert(self, source):
            seen.append(source.suffix)
            return super().convert(source)

    parse_document_bytes("process_form.xlsm", b"PK", parser=_Rec())
    assert seen == [".xlsx"]
