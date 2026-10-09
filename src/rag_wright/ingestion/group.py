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

from rag_wright.contracts.ingestion import (
    BoundaryDecider,
    IngestionContractError,
    IngestionTuning,
    RecordTableRule,
    Span,
    TableMode,
    TaggedSpan,
    Unit,
    UnitRepresentative,
)
from rag_wright.corpus.document_parser import _is_bare_heading

DEFAULT_MAX_UNIT_CHARS = IngestionTuning().max_unit_chars  # ~1,500 tokens; ~90% of contract provisions fit (ING-3)

_FURNITURE = {"page_header", "page_footer"}
_STARTS = {"heading", "title", "table"}
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


def _is_record_table(group: list[TaggedSpan], rule: RecordTableRule) -> bool:
    """A database-style table: a real header -- 3+ named columns (an unnamed first, index, column allowed), 80%+ of
    them distinct once a merged cell's name repeated across ADJACENT columns counts once -- and either a serial
    first column (in 80%+ of rows) or 8+ columns. A form's header is one section title repeated across the row, so
    it collapses to a single name and stays a block."""
    rows = [ts.span.text.strip().split("\n")[0] for ts in group if ts.span.kind == "table_row"]
    header_line = next((ln for ln in group[0].span.text.split("\n") if ln.strip().startswith("|")), "")
    header = _cells(header_line)
    named = header[1:] if header and not header[0] else header
    names = [h.lower() for i, h in enumerate(named) if i == 0 or h.lower() != named[i - 1].lower()]
    if (len(rows) < 2 or len(names) < rule.min_header_cols or not all(names)
            or len(set(names)) < rule.distinct_ratio * len(names)):
        return False
    serial = sum(_cells(r)[0].replace(".", "").isdigit() for r in rows) / len(rows)
    return serial >= rule.serial_ratio or len(header) >= rule.min_cols


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
                      max_chars: Optional[int] = None, tuning: Optional[IngestionTuning] = None,
                      table_mode: TableMode = "auto") -> list[Unit]:
    """Group a document's spans (in order) into extraction units (the engine's default `UnitGrouper`). Thresholds
    come from `tuning` (`max_chars` overrides its unit cap); `table_mode` forces every table to one unit per row
    (`record`) or to one unit (`block`) instead of deciding per table (`auto`)."""
    tuning = tuning or IngestionTuning()
    max_chars = max_chars if max_chars is not None else tuning.max_unit_chars
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
        has_rows = any(ts.span.kind == "table_row" for ts in group)
        record = has_rows and (table_mode == "record" or (table_mode == "auto"
                                                          and _is_record_table(group, tuning.record_table)))
        parts = _record_parts(group) if record else _cap(group, max_chars)
        for members, prefix in parts:
            tags = list(dict.fromkeys(t for ts in members for t in ts.tags))
            units.append(Unit(index=len(units), anchor=members[0].span, spans=[ts.span for ts in members],
                              text=_text(members, prefix), tags=tags))
    return units


def apply_unit_representative(units: Sequence[Unit], tagged: Sequence[TaggedSpan],
                              representative: UnitRepresentative) -> list[Unit]:
    """PS-R3: re-anchor and re-label each unit by its `representative` member (see `UnitRepresentative`): the chosen
    span becomes the `anchor`, its primary tag leads `tags` (the grouper's tags follow, de-duplicated). Raises
    `IngestionContractError` when the hook returns a span that is not a member of the unit."""
    by_id = {ts.span.span_id: ts for ts in tagged}
    out: list[Unit] = []
    for unit in units:
        members = [by_id[s.span_id] for s in unit.spans if s.span_id in by_id]
        if not members:
            out.append(unit)
            continue
        rep = representative(members)
        if rep.span.span_id not in {m.span.span_id for m in members}:
            raise IngestionContractError(
                f"unit {unit.index}: the representative {rep.span.span_id!r} is not a member of the unit")
        tags = [t for t in dict.fromkeys([rep.primary_tag, *unit.tags]) if t]
        out.append(unit.model_copy(update={"anchor": rep.span, "tags": tags}))
    return out
