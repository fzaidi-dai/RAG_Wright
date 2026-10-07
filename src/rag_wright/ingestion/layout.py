"""ING-2 (ADR-0124): the parse's layout, projected onto chunk text as engine `LayoutItem`s.

A chunk's text is the `"\\n\\n"`-join of the stripped texts of the content items it covers (`content_items`, in
reading order), so each item is located in its chunk with a forward cursor, the same way the page map is built.
An item the chunker hard-split across chunks is not contiguous and is left unlocated (best-effort); the segmenter
fills any gap from the text itself (`text_layout`). `text_layout` is also the whole layout for a text-only source.
"""
from __future__ import annotations

import re
from typing import Any, Sequence

from rag_wright.contracts.ingestion import LayoutItem, LayoutKind

# docling's item labels -> the engine's layout kinds (hooks never see docling's own label set)
_KIND: dict[str, LayoutKind] = {
    "title": "title",
    "section_header": "heading", "field_heading": "heading",
    "text": "paragraph", "paragraph": "paragraph", "reference": "paragraph", "handwritten_text": "paragraph",
    "list_item": "list_item", "field_item": "list_item",
    "table": "table", "document_index": "table",
    "caption": "caption", "footnote": "footnote",
    "page_header": "page_header", "page_footer": "page_footer",
    "code": "code", "formula": "formula",
    "form": "form", "key_value_region": "form", "field_region": "form", "field_key": "form", "field_value": "form",
    "field_hint": "form", "checkbox_selected": "form", "checkbox_unselected": "form", "empty_value": "form",
}


def layout_kind(label: Any) -> LayoutKind:
    """A docling item label (enum or string) -> the engine's layout kind; anything unmapped is `other`."""
    return _KIND.get(str(getattr(label, "value", label)), "other")


def chunk_layouts(document: Any, chunk_texts: Sequence[str]) -> list[list[LayoutItem]]:
    """The parsed document's layout per chunk, in chunk-relative offsets, aligned to `chunk_texts`."""
    from rag_wright.corpus.document_parser import content_items

    items = [(it, (it.text or "").strip()) for it in content_items(document)]
    items = [(it, t) for it, t in items if t]
    out: list[list[LayoutItem]] = []
    k = 0  # next unplaced item
    for text in chunk_texts:
        layout: list[LayoutItem] = []
        cursor = 0
        while k < len(items):
            item, t = items[k]
            pos = text.find(t, cursor)
            if pos < 0:
                break  # belongs to a later chunk (or was hard-split: left unlocated)
            layout.append(LayoutItem(kind=layout_kind(item.label), text=t, start=pos, end=pos + len(t),
                                     level=item.level, pages=[item.page] if item.page else []))
            cursor = pos + len(t)
            k += 1
        out.append(layout)
    return out


_TABLE_LINE = re.compile(r"^[ \t]*\|")
_LIST_LINE = re.compile(r"^[ \t]*(?:[-*•▪◦]|\(?\d{1,3}[.)]|\(?[a-zA-Z][.)])\s+\S")
_MD_HEADING = re.compile(r"^#{1,6}\s+\S")


def text_layout(text: str) -> list[LayoutItem]:
    """A layout recovered from plain text alone: blank-line blocks; a run of pipe-led lines is a `table`; a
    bullet/number-led line is a `list_item`; a markdown `#` line is a `heading`; anything else is a `paragraph`."""
    items: list[LayoutItem] = []
    for block in re.finditer(r"\S(?:.*?\S)??(?=\n[ \t]*\n|\s*\Z)", text, re.S):
        start, body = block.start(), block.group()
        lines = list(re.finditer(r"[^\n]+", body))
        if len(lines) >= 2 and all(_TABLE_LINE.match(m.group()) for m in lines):
            items.append(LayoutItem(kind="table", text=body, start=start, end=block.end()))
            continue
        if all(_LIST_LINE.match(m.group()) for m in lines):
            for m in lines:
                items.append(LayoutItem(kind="list_item", text=m.group().strip(), start=start + m.start(),
                                        end=start + m.end()))
            continue
        kind: LayoutKind = "heading" if len(lines) == 1 and _MD_HEADING.match(body) else "paragraph"
        items.append(LayoutItem(kind=kind, text=body, start=start, end=block.end()))
    return items
