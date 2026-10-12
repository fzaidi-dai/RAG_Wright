"""PS-31: a public, locked PDF page count, so a product never calls pypdfium2 itself (RuleWright's upload sizing
did, beside the engine's parses: the race of engine issue 0052)."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from rag_wright.api import pdf_page_count

PDF = Path(__file__).resolve().parents[1] / "fixtures" / "table-bearing-contract.pdf"


def test_counts_pages_from_a_path_or_bytes():
    assert pdf_page_count(PDF) == pdf_page_count(PDF.read_bytes()) == pdf_page_count(str(PDF)) >= 1


def test_an_unreadable_pdf_raises_a_value_error():
    with pytest.raises(ValueError, match="not a readable PDF"):
        pdf_page_count(b"%PDF-1.7 truncated")


def test_it_waits_for_a_pdf_parse_in_another_thread():
    from rag_wright.util.pdfium import pdf_work

    held, done = threading.Event(), {}

    def parse():
        with pdf_work("other.pdf"):
            held.set()
            time.sleep(0.3)
        done["parse_end"] = time.monotonic()

    t = threading.Thread(target=parse)
    t.start()
    held.wait()
    pdf_page_count(PDF)
    counted = time.monotonic()
    t.join()
    assert counted >= done["parse_end"]  # it ran only after the parse released the lock
