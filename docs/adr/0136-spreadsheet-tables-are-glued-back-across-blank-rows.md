# ADR-0136: A spreadsheet table cut at its blank rows is glued back together after the parse

**Status:** accepted · **Date:** 2026-10-12 · **Related:** ADR-0124 (ING-4a hidden sheets, ING-6 embedded files,
ING-7 `table_rows`), EV-2 (gold import), PS-26

## Context

docling cuts a sheet into separate tables at every blank row (its Excel backend's `gap_tolerance` defaults to 0),
and the whole engine reads each table's first row as its header: `table_rows`, the compact table text, the segmenter,
record units and the row-span map. A database sheet with blank rows between blocks therefore lost the first record
after each blank row, and the rows after it were keyed under that record's values (a 5-record fixture kept 3).
Raising `gap_tolerance` is not a fix: it also merges a title block and a different table below into one table.

## Decision

After the parse (`capabilities/sheet_tables.py`), every table piece directly below another in the same columns,
with only blank rows between, is decided by its first row:

- **data, glued on**: the number or date columns agree (every data row above has a number or date there, the
  header does not, and the first row does), or, with no such columns, the formatting does (the header is bold or
  filled differently from the data rows, read with openpyxl, and the first row is formatted like the data);
- **header, left apart**: the same header repeated, a header-formatted row, text in a number column, a table above
  whose top row does not name every column its rows use (a form's section label), or a first row that does not fill
  the columns the rows above fill;
- **unsure, left apart and warned**: a text-only table with no formatting difference, which the file alone cannot
  decide. `evaluate_ingestion` lists it in a new `warnings` list (never a failure).

A glued table keeps each block's sheet rectangle as another `prov` entry, so an embedded file anchored on a sheet
cell still maps to its table row (`_table_at` walks the blocks). The merge runs on a fresh parse and once on a parse
cached before it (the ING-4a pattern), so no cache key changes. `openpyxl` is declared (it was installed through
docling).

## Consequences

- A database sheet's records survive its blank rows; titles, forms and different tables are parsed as before.
- On the five TexWright workbooks (measured locally, structure only): nothing glued, 63 tables and 596 rows as
  before, 2 warnings (one-column lists separated by a blank row).
- A sheet shape none of the rules covers is added as a new case when a customer's file shows it.
