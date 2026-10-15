"""PS-47: find cached parses that hold text on a page with no ink.

Before PS-46 a blank PDF page was escalated to the vision model, which invents a page when asked to transcribe an
empty one, and the parse kept it (TexWright, 2026-10-15). The parse cache is keyed on the source's content, so the
fix does not redo those parses. This lists them: every page that is pure white in the source PDF (no ink, no text
layer) yet has text in its cached parse. Delete the finding's `cache_files` and re-ingest the document.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Union

from pydantic import BaseModel

Source = Union[str, Path, tuple[str, bytes]]


class BlankPageText(BaseModel):
    """One cached parse with text on blank pages. `source` is the PDF's path (or the name given with its bytes),
    `pages` maps each blank page to the text the cached parse holds there, and `cache_files` are the cached parse(s)
    and their OCR sidecar: delete them, then re-ingest the document so it is parsed again."""

    source: str
    pages: dict[int, str]
    cache_files: list[str]


def find_blank_pages_with_text(cache_dir: Any, sources: Iterable[Source] = (), *,
                               progress: Optional[Callable[[int, int], None]] = None) -> list[BlankPageText]:
    """Audit the parse cache under `cache_dir` (the one given to `parse_document`, or to `aingest`, which keeps its
    parses in `parsed/`; searched recursively) for text on blank pages. `sources` are the documents' PDFs, as paths or `(name, bytes)`; files embedded in a parsed
    package are stored in the cache and audited without being passed. A source that is not a PDF, or has no cached
    parse, is skipped. `progress(done, total)` is called after each PDF. Raises `FileNotFoundError` when
    `cache_dir` does not exist."""
    cache = Path(cache_dir)
    if not cache.is_dir():
        raise FileNotFoundError(f"no parse cache at {cache}")
    pdfs = [(name, data) for name, data in (_read(s) for s in sources) if data.startswith(b"%PDF")]
    pdfs += [(str(p), p.read_bytes()) for p in sorted(cache.glob("**/*.embedded/*"))
             if p.is_file() and p.read_bytes()[:4] == b"%PDF"]
    found: list[BlankPageText] = []
    for i, (name, data) in enumerate(pdfs, 1):
        hit = _audit(cache, name, data)
        if hit is not None:
            found.append(hit)
        if progress is not None:
            progress(i, len(pdfs))
    return found


def _read(source: Source) -> tuple[str, bytes]:
    if isinstance(source, tuple):
        return source
    return str(source), Path(source).read_bytes()


def _audit(cache: Path, name: str, data: bytes) -> Optional[BlankPageText]:
    from docling_core.types.doc.document import DoclingDocument

    from rag_wright.capabilities.parsing import _render_gray_pages, _text_layer_pages
    from rag_wright.capabilities.scan_quality import _INK_FRAC_BLANK, _page_texts, image_quality

    h = hashlib.sha256(data).hexdigest()[:16]
    manifests = sorted([*cache.rglob(f"*.{h}.json"), *cache.rglob(f"*.{h}.nohidden.json")])
    if not manifests:
        return None
    text_layer = _text_layer_pages(data)
    blank = {pg for pg, img in _render_gray_pages(data).items()
             if pg not in text_layer and image_quality(img)["ink_frac"] <= _INK_FRAC_BLANK}
    if not blank:
        return None
    pages: dict[int, str] = {}
    holding: list[Path] = []
    for manifest in manifests:
        try:
            texts = _page_texts(DoclingDocument.load_from_json(manifest))
        except Exception:  # noqa: BLE001 - another cache's file under the same name pattern, not a parse
            continue
        invented = {pg: texts[pg] for pg in sorted(blank) if texts.get(pg)}
        if invented:
            pages.update(invented)
            holding.append(manifest)
    if not pages:
        return None
    sidecars = {m.parent / f"{m.name.split(f'.{h}.')[0]}.{h}.ocr.json" for m in holding}
    return BlankPageText(source=name, pages=dict(sorted(pages.items())),
                         cache_files=[str(p) for p in holding] + sorted(str(s) for s in sidecars if s.exists()))
