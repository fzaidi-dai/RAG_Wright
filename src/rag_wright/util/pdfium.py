"""PS-29 (engine issue 0052): the engine's PDF work is serialised, and its one door to pdfium is locked.

pdfium is not thread-safe, and the engine parses several documents at once (`document_concurrency`). Two layers:

- `pdf_work()`: ONE process-wide re-entrant lock around each PDF's parse (docling's conversion of it plus the
  engine's own probes). Concurrent PDF parses crashed in libpdfium even with every engine pdfium call locked:
  pdfium objects created during a conversion can be freed later by the garbage collector, on any thread, outside
  any lock. So a PDF parse runs alone and ends with a garbage collection INSIDE the lock, so nothing it created is
  freed later beside another document's parse. Everything else in ingestion (chunk, segment, embed, extract, model
  calls) stays concurrent; within one document docling's own pipeline threads run as docling designed them.
- `open_pdf(source)`: the engine's only way to pypdfium2. It takes `pdf_work()` and then docling's own pdfium lock
  (`docling.utils.locks.pypdfium2_lock`; always in that order), and closes the document -- which closes its pages
  and text pages first -- before releasing them. `tests/arch/test_pdfium_door.py` fails on any other import of
  pypdfium2 under `src/rag_wright`.
"""
from __future__ import annotations

import gc
import logging
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Union

_log = logging.getLogger(__name__)
PDF_WORK_LOCK = threading.RLock()
_SLOW_WAIT_S = 10.0


@contextmanager
def pdf_work(label: str = "PDF") -> Iterator[None]:
    """Run a PDF's parse alone in the process (re-entrant in one thread), and collect garbage before letting the
    next one in. Logs when the wait for another document's parse exceeds 10 s."""
    t0 = time.monotonic()
    with PDF_WORK_LOCK:
        waited = time.monotonic() - t0
        if waited > _SLOW_WAIT_S:
            _log.info("%s waited %.0fs for another PDF's parse (PDF parses run one at a time, PS-29)", label, waited)
        try:
            yield
        finally:
            gc.collect()  # free any pdfium object left by this parse NOW, under the lock


@contextmanager
def open_pdf(source: Union[str, Path, bytes]) -> Iterator[Any]:
    """A `pypdfium2.PdfDocument` for `source` (a path or the PDF's bytes), used and closed under the PDF lock and
    docling's pdfium lock. Do every pdfium call inside the block; nothing from it may outlive the block."""
    import pypdfium2
    from docling.utils.locks import pypdfium2_lock

    with PDF_WORK_LOCK, pypdfium2_lock:
        pdf = pypdfium2.PdfDocument(source if isinstance(source, bytes) else str(source))
        try:
            yield pdf
        finally:
            pdf.close()
