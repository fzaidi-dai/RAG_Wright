"""issue 0032 / CU-B5: map a span's canonical-text character range to its source page(s).

Docling records each parsed item's `prov[0].page_no` (+ a best-effort bbox); `content_items` now carries it
(`ContentItem.page`/`.bbox`). The chunker builds a canonical document text (`_SEP`-join of the chunk texts,
each a `_SEP`-join of the stripped item texts) that span `doc_start`/`doc_end` offsets index into. This module
reconstructs a `[char_range) -> page` map over that canonical text by locating each item's text in reading
order, so a span's pages fall out of the char range it already has -- correctly handling a clause that crosses
a page boundary (the pages come back as a LIST). Page-only is enough for the honest scanned-PDF fallback
("page 7 -- exact location unavailable"); bbox is carried best-effort where the parser produced one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

BBox = tuple[float, float, float, float]


@dataclass(frozen=True)
class PageRange:
    """One content item's [start, end) character range in the canonical document text, its 1-based source
    page, and a best-effort bbox (l, t, r, b on that page) when the parser produced one."""

    start: int
    end: int
    page: int
    bbox: Optional[BBox]


def build_page_offset_map(items: list[Any], canonical_text: str) -> list[PageRange]:
    """Reconstruct the page map for `canonical_text` by locating each content item's STRIPPED text in reading
    order with a forward cursor (the canonical text is the `_SEP`-join of exactly those stripped item texts).

    Robust to the chunker's below-floor merges and bare-heading folds -- they regroup which items land in
    which chunk but never change the item-text SEQUENCE, so the cursor still finds each in order. The only
    lossy case is an over-cap item hard-split across chunks (rare): its text is not contiguous, so it is left
    unlocated (best-effort). Items with no page or empty text contribute nothing."""
    out: list[PageRange] = []
    cursor = 0
    for it in items:
        page = getattr(it, "page", None)
        text = (getattr(it, "text", "") or "").strip()
        if page is None or not text:
            continue
        idx = canonical_text.find(text, cursor)
        if idx == -1:  # a merged/reordered edge: fall back to a global search before giving up
            idx = canonical_text.find(text)
            if idx == -1:
                continue
        out.append(PageRange(idx, idx + len(text), int(page), getattr(it, "bbox", None)))
        cursor = idx + len(text)
    return out


def pages_for(
    page_map: list[PageRange], doc_start: Optional[int], doc_end: Optional[int]
) -> tuple[list[int], Optional[BBox]]:
    """The distinct source pages a `[doc_start, doc_end)` span overlaps (sorted ascending -- a LIST, because a
    clause can cross a page boundary), plus a best-effort bbox: the box of the SINGLE overlapping item when
    exactly one overlaps and it carries a box, else `None` (a multi-item/multi-page span has no single box).
    An empty map, missing offsets, or no overlap -> `([], None)`."""
    if doc_start is None or doc_end is None:
        return [], None
    overlapping = [pr for pr in page_map if pr.start < doc_end and pr.end > doc_start]
    pages = sorted({pr.page for pr in overlapping})
    bbox = overlapping[0].bbox if len(overlapping) == 1 else None
    return pages, bbox
