"""PS-29 (engine issue 0052): every engine use of pdfium goes through `rag_wright.util.pdfium.open_pdf`, which holds
docling's pdfium lock from open to close -- pdfium is not thread-safe, and documents parse concurrently."""
from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src" / "rag_wright"
_DOOR = _SRC / "util" / "pdfium.py"
_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "table-bearing-contract.pdf"


def _imports_pdfium(path: Path) -> bool:
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import) and any(a.name.split(".")[0] == "pypdfium2" for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "pypdfium2":
            return True
    return False


def test_only_the_door_imports_pypdfium2():
    offenders = [str(p.relative_to(_SRC)) for p in _SRC.rglob("*.py") if p != _DOOR and _imports_pdfium(p)]
    assert offenders == [], f"use rag_wright.util.pdfium.open_pdf instead of pypdfium2 directly: {offenders}"


def test_the_door_holds_doclings_lock_and_closes_the_document():
    from docling.utils.locks import pypdfium2_lock

    from rag_wright.util.pdfium import open_pdf

    with open_pdf(_FIXTURE) as pdf:
        assert pypdfium2_lock.locked() and len(pdf) >= 1
        page_text = pdf[0].get_textpage()
    assert not pypdfium2_lock.locked()
    assert pdf.raw is None and page_text.raw is None  # the document and its children closed under the lock


def test_the_door_takes_bytes_and_releases_the_lock_on_an_unreadable_pdf():
    import pytest
    from docling.utils.locks import pypdfium2_lock

    from rag_wright.util.pdfium import open_pdf

    with open_pdf(_FIXTURE.read_bytes()) as pdf:
        assert len(pdf) >= 1
    with pytest.raises(Exception):
        with open_pdf(b"not a pdf"):
            pass
    assert not pypdfium2_lock.locked()


def _peak_concurrency(name: str, data: bytes) -> int:
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    from rag_wright.corpus.document_parser import parse_document_bytes

    live, peak, lock = [0], [0], threading.Lock()

    class _Parser:
        def convert(self, _path):
            with lock:
                live[0] += 1
                peak[0] = max(peak[0], live[0])
            time.sleep(0.05)
            with lock:
                live[0] -= 1
            return "doc"

    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda _i: parse_document_bytes(name, data, parser=_Parser()), range(8)))
    return peak[0]


def test_pdf_parses_run_one_at_a_time_and_other_documents_stay_concurrent():
    assert _peak_concurrency("contract.pdf", b"%PDF-1.7 ...") == 1
    assert _peak_concurrency("upload.bin", b"%PDF-1.7 ...") == 1  # a PDF by its bytes
    assert _peak_concurrency("notes.docx", b"PK\x03\x04 ...") > 1
