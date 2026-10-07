"""ING-3 (ADR-0124): the engine's default unit grouper -- domain-neutral, structure-driven.

Implements the `UnitGrouper` hook over the segmenter's spans, reading structure off `Span.kind`:

- a HEADING (or title) span starts a unit, and the content under it belongs to that unit;
- a TABLE is one unit: its header span starts it (or the heading span above it, which already holds the header) and
  its rows continue it; the first span after the last row starts a new unit;
- PAGE FURNITURE (page header/footer) and spans with no real text are dropped (they stay in the span index);
- a CHUNK change ends a unit;
- an optional `decider` adjudicates heading-like lines the parse labelled as plain text (one batched call; on
  error, or with no decider, they do not split);
- a unit is capped at `max_chars` and split at span boundaries; a split table's continuation units repeat the
  header row in their text, so the extractor keeps the column context;
- a DATABASE-style table (a real header -- every cell named, all distinct -- and either a serial first column or
  8+ columns) is one unit PER ROW, each carrying the header in its text: its rows are independent records. A form
  grid or key-value table (rows are fields of ONE record) stays one unit (ING-4a).

No domain rules: the reference contract pack passes its own provision grouper.
"""
from __future__ import annotations

import re
from typing import Optional, Sequence

from rag_wright.contracts.ingestion import BoundaryDecider, Span, TaggedSpan, Unit
from rag_wright.corpus.document_parser import _is_bare_heading

DEFAULT_MAX_UNIT_CHARS = 6000  # ~1,500 tokens; leaves ~90% of contract-provision-sized units unsplit (ING-3)

_FURNITURE = {"page_header", "page_footer"}
_STARTS = {"heading", "title", "table"}
_RECORD_MIN_COLS = 8  # a header this wide is a database, even without a serial column
_CELL_SPLIT = re.compile(r"(?<!\\)\|")


def _alnum(s: str) -> int:
    return sum(c.isalnum() for c in s)


async def _decided_starts(spans: Sequence[Span], decider: Optional[BoundaryDecider]) -> list[bool]:
    """Per span: does the decider say this heading-like plain line starts a unit? False when not asked."""
    out = [False] * len(spans)
    idx = [i for i, s in enumerate(spans) if s.kind in (None, "paragraph", "other") and _is_bare_heading(s.text)]
    if decider is None or not idx:
        return out
    try:
        verdicts = await decider([spans[i].text.strip() for i in idx])
    except Exception:  # noqa: BLE001 - an unavailable decider degrades to "no split", never a lost document
        return out
    for i, v in zip(idx, verdicts):
        out[i] = bool(v)
    return out


def _text(members: Sequence[TaggedSpan], prefix: str = "") -> str:
    body = "\n".join(ts.span.text.strip() for ts in members)
    return f"{prefix}\n{body}" if prefix else body


def _cells(line: str) -> list[str]:
    return [c.strip() for c in _CELL_SPLIT.split(line.strip().strip("|"))]


def _is_record_table(group: list[TaggedSpan]) -> bool:
    """A database-style table: a real header -- 3+ named columns (an unnamed first, index, column allowed), 80%+ of
    them distinct once a merged cell's name repeated across ADJACENT columns counts once -- and either a serial
    first column (in 80%+ of rows) or 8+ columns. A form's header is one section title repeated across the row, so
    it collapses to a single name and stays a block."""
    rows = [ts.span.text.strip().split("\n")[0] for ts in group if ts.span.kind == "table_row"]
    header_line = next((ln for ln in group[0].span.text.split("\n") if ln.strip().startswith("|")), "")
    header = _cells(header_line)
    named = header[1:] if header and not header[0] else header
    names = [h.lower() for i, h in enumerate(named) if i == 0 or h.lower() != named[i - 1].lower()]
    if len(rows) < 2 or len(names) < 3 or not all(names) or len(set(names)) < 0.8 * len(names):
        return False
    serial = sum(_cells(r)[0].replace(".", "").isdigit() for r in rows) / len(rows)
    return serial >= 0.8 or len(header) >= _RECORD_MIN_COLS


def _record_parts(group: list[TaggedSpan]) -> list[tuple[list[TaggedSpan], str]]:
    """One part per row; the header span joins the first row, and every later row carries the header as context."""
    first_row = next(i for i, ts in enumerate(group) if ts.span.kind == "table_row")
    header = "\n".join(ts.span.text.strip() for ts in group[:first_row])
    parts = [(group[:first_row + 1], "")]
    parts += [([ts], header) for ts in group[first_row + 1:]]
    return parts


def _cap(group: list[TaggedSpan], max_chars: int) -> list[tuple[list[TaggedSpan], str]]:
    """Split one group at span boundaries so each part's text fits `max_chars` (a single over-long span stays whole).
    Returns (members, header prefix) pairs; a table's continuation parts carry its header row as the prefix."""
    is_table = any(ts.span.kind == "table_row" for ts in group)
    header = group[0].span.text.strip() if is_table else ""
    parts: list[tuple[list[TaggedSpan], str]] = []
    cur: list[TaggedSpan] = []
    for ts in group:
        prefix = header if parts else ""
        if cur and len(_text([*cur, ts], prefix)) > max_chars:
            parts.append((cur, prefix))
            cur = []
        cur.append(ts)
    if cur:
        parts.append((cur, header if parts else ""))
    return parts


async def group_units(spans: Sequence[TaggedSpan], *, decider: Optional[BoundaryDecider] = None,
                      max_chars: int = DEFAULT_MAX_UNIT_CHARS) -> list[Unit]:
    """Group a document's spans (in order) into extraction units (the engine's default `UnitGrouper`)."""
    kept = [ts for ts in spans if ts.span.kind not in _FURNITURE and _alnum(ts.span.text) >= 2]
    decided = await _decided_starts([ts.span for ts in kept], decider)
    groups: list[list[TaggedSpan]] = []
    for i, ts in enumerate(kept):
        s = ts.span
        in_table = bool(groups) and any(m.span.kind == "table_row" for m in groups[-1])
        new = (not groups or s.parent_chunk_id != kept[i - 1].span.parent_chunk_id or s.kind in _STARTS
               or decided[i] or (in_table and s.kind != "table_row"))
        if new:
            groups.append([ts])
        else:
            groups[-1].append(ts)
    units: list[Unit] = []
    for group in groups:
        parts = _record_parts(group) if _is_record_table(group) else _cap(group, max_chars)
        for members, prefix in parts:
            tags = list(dict.fromkeys(t for ts in members for t in ts.tags))
            units.append(Unit(index=len(units), anchor=members[0].span, spans=[ts.span for ts in members],
                              text=_text(members, prefix), tags=tags))
    return units
