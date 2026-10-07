"""ING-2 (ADR-0124): the engine's default segmenter -- domain-neutral, layout-driven, deterministic.

Implements the `Segmenter` hook. The parse's layout decides the structure; plain NLP rules decide the rest:

- a TABLE yields one span per row (the header row and its separator row together), so a citation lands on the row
  that holds the value; the unit grouper (ING-3) keeps the whole table together for extraction;
- a LIST ITEM, FORM field, CODE/FORMULA block or page furniture is one span;
- PROSE (paragraph, caption, footnote) splits into sentences: a terminator `.?!` followed by whitespace and a capital,
  digit, quote or opener -- never after a common abbreviation (`e.g.`, `No.`, `approx.`, ...);
- a HEADING joins the span that follows it (a bare heading carries no content to cite);
- a fragment with fewer than `min_alnum` letters/digits (a stray `N`, a page number) folds into a neighbour.

Text the layout does not cover (and the whole chunk for a text-only source) is laid out by `text_layout`. The spans
tile the chunk text byte-faithfully (`check_tiling`). No domain rules: a domain with its own conventions overrides
the hook (the reference contract pack passes its legal `segment_clause`).
"""
from __future__ import annotations

import re
from typing import Sequence

from rag_wright.contracts.ingestion import LayoutItem, Span
from rag_wright.ingestion.layout import text_layout

DEFAULT_MIN_ALNUM = 2

# Common English/technical abbreviations whose '.' does not end a sentence (lower-cased, without the final dot).
_ABBREV = {
    "e.g", "i.e", "etc", "vs", "cf", "approx", "appr", "ca", "no", "nos", "ref", "req", "fig", "figs", "dia", "min",
    "max", "avg", "std", "vol", "est", "dept", "mr", "mrs", "ms", "dr", "st", "jr", "sr", "inc", "ltd", "co",
}
_SENTENCE_END = re.compile(r"[.?!][\"')\]]*\s+(?=[A-Z0-9\"'(\[])")
_TOKEN_BEFORE = re.compile(r"(\S+)$")
_LIST_MARKER = re.compile(r"\(?(?:\d{1,3}(?:\.\d{1,3})*|[ivxlcdm]{1,4}|[a-zA-Z])\)?\.", re.IGNORECASE)
_SEPARATOR_ROW = re.compile(r"^[ \t]*\|?[ \t:|-]*-[ \t:|-]*\|?[ \t]*$")
_PROSE = {"paragraph", "caption", "footnote", "other"}


def _sentence_cuts(text: str, start: int, end: int) -> list[int]:
    cuts: list[int] = []
    for m in _SENTENCE_END.finditer(text, start, end):
        before = _TOKEN_BEFORE.search(text, start, m.start() + 1)
        token = before.group(1).lower().rstrip(".?!\"')]") if before else ""
        if m.group()[0] == "." and token.lstrip("([\"'") in _ABBREV:
            continue
        if before and not text[start:before.start()].strip() and _LIST_MARKER.fullmatch(before.group(1)):
            continue  # the item's own list number ('9.', '12.1.', 'iv.') opens it; it does not end a sentence
        cuts.append(m.end())
    return cuts


def _table_cuts(text: str, start: int, end: int) -> list[int]:
    """One cut per table row; a separator row stays with the row above it (the header)."""
    cuts: list[int] = []
    for m in re.finditer(r"[^\n]+", text[start:end]):
        if m.start() == 0 or _SEPARATOR_ROW.match(m.group()):
            continue
        cuts.append(start + m.start())
    return cuts


def _covering_layout(text: str, layout: Sequence[LayoutItem]) -> list[LayoutItem]:
    """The layout items in order, with any stretch of real text they leave uncovered laid out from the text."""
    items: list[LayoutItem] = []
    pos = 0
    for it in sorted(layout, key=lambda i: i.start):
        if it.start < pos:
            continue  # an overlapping item (should not happen): keep the first
        if text[pos:it.start].strip():
            items += [g.model_copy(update={"start": g.start + pos, "end": g.end + pos})
                      for g in text_layout(text[pos:it.start])]
        items.append(it)
        pos = it.end
    if text[pos:].strip():
        items += [g.model_copy(update={"start": g.start + pos, "end": g.end + pos}) for g in text_layout(text[pos:])]
    return items


def _alnum(s: str) -> int:
    return sum(c.isalnum() for c in s)


def segment_layout(chunk_id: str, text: str, layout: Sequence[LayoutItem], *,
                   min_alnum: int = DEFAULT_MIN_ALNUM) -> list[Span]:
    """Segment one chunk into spans (the engine's default `Segmenter`)."""
    if not text:
        return []
    # 1) pieces: (start, kind) -- where each span may begin
    pieces: list[tuple[int, str]] = []
    for it in _covering_layout(text, layout):
        pieces.append((it.start, it.kind))
        if it.kind == "table":
            pieces += [(c, "table_row") for c in _table_cuts(text, it.start, it.end)]
        elif it.kind in _PROSE:
            pieces += [(c, it.kind) for c in _sentence_cuts(text, it.start, it.end)]
    # 2) which piece starts keep a cut. A tiny fragment folds BACK into the previous span (forward only when it
    #    opens the chunk); a heading joins what follows it, or the previous span when nothing but fragments follow.
    bounds = [p for p, _ in pieces[1:]] + [len(text)]
    tiny = [kind != "table_row" and _alnum(text[p:bounds[i]]) < min_alnum for i, (p, kind) in enumerate(pieces)]
    trailing = [all(tiny[i + 1:]) for i in range(len(pieces))]  # nothing but fragments after piece i
    cuts = [0]  # the first span starts at 0 (absorbing any leading whitespace)
    kinds = [_first_kind(pieces, tiny)]  # ING-3: what each span starts with (`Span.kind`)
    for i in range(1, len(pieces)):
        p, kind = pieces[i]
        heading = kind in ("heading", "title")
        if pieces[i - 1][1] in ("heading", "title") and not trailing[i - 1]:
            continue  # a heading joins the span that follows it
        if i == 1 and tiny[0]:
            continue  # a fragment opening the chunk folds forward into this piece
        if tiny[i] or (heading and trailing[i]):
            continue  # a fragment (or a heading that ends the chunk) folds back into the previous span
        if p > cuts[-1]:
            cuts.append(p)
            kinds.append(kind)
    cuts.append(len(text))
    return [Span(span_id=f"{chunk_id}#{i}", parent_chunk_id=chunk_id, span_index=i, start=s, end=e, text=text[s:e],
                 kind=kinds[i])
            for i, (s, e) in enumerate(zip(cuts, cuts[1:]))]


def _first_kind(pieces: list[tuple[int, str]], tiny: list[bool]) -> str | None:
    """The kind of a chunk's first span: its first piece, or the next one when a fragment folded forward into it."""
    if not pieces:
        return None
    return pieces[1][1] if len(pieces) > 1 and tiny[0] else pieces[0][1]
