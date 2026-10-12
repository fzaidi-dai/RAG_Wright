"""EV-2: import a gold set from a domain expert's own file -- CSV, XLSX or JSONL -- through a column mapping.

The header is the first row that holds every mapped column (matched with spaces collapsed and case ignored), so a
title or notes above it are skipped; empty rows are skipped; values are kept exactly, ends trimmed (inner line breaks
kept). XLSX is read cell by cell (openpyxl), every visible sheet with the header, or the one `sheet` named (hidden
ones too); numbers and dates become text. JSONL keeps its types. Each item records where it came from in
`metadata["source"]`; its `id` is the mapped column, else where the row is (`row-<n>`, `line-<n>`, `<sheet>!<n>`).
Anything that would make the gold wrong -- a missing column, a row without its id, a duplicate id, an unknown
provenance -- is refused with its place, never skipped.
"""
from __future__ import annotations

import asyncio
import csv
import datetime as dt
import io
import json
from pathlib import Path
from typing import Any, Iterable, Optional, Union

from pydantic import BaseModel, Field, ValidationError

from rag_wright.contracts.evals import GoldItem, GoldSet, Provenance
from rag_wright.contracts.ingestion import IngestSource

_PROVENANCE = ("hand", "silver", "generated")


class GoldImportError(ValueError):
    """The file cannot be read into a correct gold set; the message says where and why."""


class GoldMapping(BaseModel):
    """Which columns (or JSONL keys) hold what. `input` / `expected`: one column gives its value, several give a
    `{column: value}` record. `id`: the column with a stable item id (None = where the row is). `group`: the
    leakage-safe grouping key's column. `provenance`: a column of `hand` / `silver` / `generated` (blank = the
    default). `metadata`: columns copied onto each item."""

    input: Union[str, list[str]]
    expected: Union[str, list[str]]
    id: Optional[str] = None
    group: Optional[str] = None
    provenance: Optional[str] = None
    metadata: list[str] = Field(default_factory=list)

    def columns(self) -> list[str]:
        cols = [*_as_list(self.input), *_as_list(self.expected), *self.metadata]
        return cols + [c for c in (self.id, self.group, self.provenance) if c]


def _as_list(value: Union[str, list[str]]) -> list[str]:
    return [value] if isinstance(value, str) else list(value)


def _key(name: Any) -> str:
    return " ".join(str(name or "").split()).casefold()


def _cell(value: Any) -> Any:
    """A spreadsheet or CSV cell as text: ends trimmed, Windows line breaks unified, whole numbers without `.0`."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (dt.datetime, dt.date)):
        midnight = isinstance(value, dt.datetime) and value.time() == dt.time(0)
        return value.date().isoformat() if midnight else value.isoformat()
    return str(value).replace("\r\n", "\n").strip()


def _clean(value: Any) -> Any:
    return value.replace("\r\n", "\n").strip() if isinstance(value, str) else value


def load_gold_set(source: Union[str, Path, IngestSource], mapping: GoldMapping, *, name: str, version: str,
                  provenance: Provenance = "hand", local_only: bool = True, sheet: Optional[str] = None,
                  description: str = "") -> GoldSet:
    """Read a gold set from an expert's CSV, XLSX or JSONL file (a path, or an `IngestSource` holding an upload's
    bytes) through `mapping`. `provenance` is every item's unless the mapping names a provenance column; `sheet`
    reads one XLSX sheet (hidden ones too). Raises `GoldImportError` on anything that would make the gold wrong."""
    src = source if isinstance(source, IngestSource) else IngestSource(path=str(source))
    label = src.display_name
    suffix = Path(label).suffix.lower()
    data = src.read_bytes()
    if suffix == ".csv":
        tables = [("row", label, _rows_csv(data))]
    elif suffix in (".xlsx", ".xlsm"):
        tables = _sheets_xlsx(data, sheet)
    elif suffix == ".jsonl":
        tables = [("line", label, _lines_jsonl(data, label))]
    else:
        raise GoldImportError(f"{label}: a gold file is CSV, XLSX or JSONL (got {suffix or 'no extension'})")
    items: list[GoldItem] = []
    misses = []
    for where, sheet_name, rows in tables:
        found = _items(rows, mapping, where, sheet_name, label, provenance, suffix == ".jsonl")
        if found is None:
            misses.append(sheet_name)
        else:
            items += found
    if misses and not items:
        raise GoldImportError(_missing(mapping, tables, label, suffix))
    try:
        return GoldSet(name=name, version=version, items=items, local_only=local_only, description=description)
    except ValidationError as exc:
        raise GoldImportError(f"{label}: {exc.errors()[0]['msg']}") from exc


async def aload_gold_set(source: Union[str, Path, IngestSource], mapping: GoldMapping, *, name: str,
                         version: str, provenance: Provenance = "hand", local_only: bool = True,
                         sheet: Optional[str] = None, description: str = "") -> GoldSet:
    """`load_gold_set` off the event loop, for a web backend."""
    return await asyncio.to_thread(load_gold_set, source, mapping, name=name, version=version,
                                   provenance=provenance, local_only=local_only, sheet=sheet,
                                   description=description)


def _rows_csv(data: bytes) -> list[tuple[int, list[Any]]]:
    text = data.decode("utf-8-sig")
    return list(enumerate(csv.reader(io.StringIO(text, newline="")), 1))  # numbered as a spreadsheet shows them


def _sheets_xlsx(data: bytes, sheet: Optional[str]) -> list[tuple[str, str, list]]:
    import openpyxl

    try:
        book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - not a readable workbook
        raise GoldImportError(f"not a readable XLSX workbook: {exc}") from exc
    if sheet is not None and sheet not in book.sheetnames:
        raise GoldImportError(f"no sheet {sheet!r} (sheets: {', '.join(book.sheetnames)})")
    names = [sheet] if sheet is not None else [n for n in book.sheetnames if book[n].sheet_state == "visible"]
    return [("sheet", n, [(i, list(r)) for i, r in enumerate(book[n].iter_rows(values_only=True), 1)])
            for n in names]


def _lines_jsonl(data: bytes, label: str) -> list[tuple[int, dict]]:
    out = []
    for n, line in enumerate(data.decode("utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise GoldImportError(f"{label}: line {n} is not JSON: {exc.msg}") from exc
        if not isinstance(row, dict):
            raise GoldImportError(f"{label}: line {n} is not a JSON object")
        out.append((n, row))
    return out


def _items(rows: list, mapping: GoldMapping, where: str, sheet_name: str, label: str, provenance: str,
           jsonl: bool) -> Optional[list[GoldItem]]:
    wanted = mapping.columns()
    if jsonl:
        header_at, rows_after = 0, rows
        index = None
    else:
        header_at = next((i for i, (_n, r) in enumerate(rows)
                          if {_key(c) for c in wanted} <= {_key(v) for v in r}), None)
        if header_at is None:
            return None
        header = [_key(v) for v in rows[header_at][1]]
        index = {c: header.index(_key(c)) for c in wanted}
        rows_after = rows[header_at + 1:]

    def value(row: Any, col: str) -> Any:
        if jsonl:
            hit = next((k for k in row if _key(k) == _key(col)), None)
            return _clean(row[hit]) if hit is not None else None
        i = index[col]
        return _cell(row[i]) if i < len(row) else ""

    def field(row: Any, cols: Union[str, list[str]]) -> Any:
        return value(row, cols) if isinstance(cols, str) else {c: value(row, c) for c in cols}

    items = []
    for n, row in rows_after:
        if not jsonl and all(value(row, c) in ("", None) for c in wanted):
            continue  # an empty row
        place = (f"{label}: {sheet_name} row {n}" if where == "sheet" else f"{label}: {where} {n}")
        if jsonl and any(value(row, c) is None for c in [*_as_list(mapping.input), *_as_list(mapping.expected)]):
            missing = [c for c in [*_as_list(mapping.input), *_as_list(mapping.expected)] if value(row, c) is None]
            raise GoldImportError(f"{place}: no {', '.join(missing)}")
        if mapping.id:
            item_id = str(value(row, mapping.id) or "").strip()
            if not item_id:
                raise GoldImportError(f"{place}: no {mapping.id}")
        else:
            item_id = f"{sheet_name}!{n}" if where == "sheet" else f"{where}-{n}"
        prov = provenance
        if mapping.provenance:
            given = str(value(row, mapping.provenance) or "").strip().lower()
            if given and given not in _PROVENANCE:
                raise GoldImportError(f"{place}: provenance {given!r} is not one of {', '.join(_PROVENANCE)}")
            prov = given or provenance
        group = value(row, mapping.group) if mapping.group else None
        meta = {c: value(row, c) for c in mapping.metadata}
        meta["source"] = place
        items.append(GoldItem(id=item_id, input=field(row, mapping.input), expected=field(row, mapping.expected),
                              group=str(group) if group not in (None, "") else None, provenance=prov,
                              metadata=meta))
    return items


def _missing(mapping: GoldMapping, tables: Iterable, label: str, suffix: str) -> str:
    wanted = {_key(c) for c in mapping.columns()}
    seen, missing = [], set(mapping.columns())
    for _where, sheet_name, rows in tables:
        heads = [list(r) if isinstance(r, dict) else [_cell(v) for v in r if _cell(v)] for _n, r in rows]
        best = max(heads, key=lambda h: len(wanted & {_key(v) for v in h}), default=[])
        if best:
            have = {_key(v) for v in best}
            missing &= {c for c in mapping.columns() if _key(c) not in have}
            seen.append(f"{sheet_name}: {', '.join(best)}" if suffix in (".xlsx", ".xlsm") else ", ".join(best))
    return (f"{label}: no header row holds every mapped column; missing {', '.join(sorted(missing))} -- closest "
            f"header found: {' | '.join(seen) or 'none'}")
