# ADR-0141: The cap's hard split cuts between lines and repeats a table's header

**Status:** accepted · **Date:** 2026-11-05 · **Related:** PS-60, TexWright's request
`2026-11-05-export-the-default-segmenter-on-pack-sdk.md`, ADR-0069 (tables chunked in reading order), ADR-0124
(ING-2 the default segmenter, ING-7 `table_rows`), ADR-0136 (spreadsheet tables glued across blank rows)

## Context

A chunk is a run of whole parsed items, and the token cap is enforced last: a section over the cap that the
discoverer cannot subdivide (a single table is one item) is hard-split. The split cut every `token_cap * 4`
characters, wherever that fell. In a table it fell mid-row, and three things followed. The table item was found
whole in no chunk, so `chunk_layouts` placed it nowhere, and the unplaced item also stopped every later item in the
document from getting its parsed layout. Each piece then fell back to the text layout: the first piece still
opened with the table's header, but a continuation piece began mid-row, read as one paragraph, and gave ONE span to
all its rows. With no row spans, no unit carried a `TableRow`, so a record extractor never saw those rows, and the
ingest reported success. TexWright lost 4 of 140 records this way; a 140-record, 76-column synthetic workbook lost
205 of 280 rows.

## Decision

- The hard split cuts at the last blank line in the second half of the window, else the last line break, else the
  last space, and only a run with none of them at a fixed length. The whitespace character it cuts at is dropped.
  Pieces stay strictly under the cap.
- A cut inside a pipe table repeats the table's header and separator rows at the top of the next piece, so every
  piece is a well-formed table, each row is whole in one chunk, and the segmenter, the unit grouper's record-table
  test and the row-to-span map work unchanged on every piece. A header half the cap or longer is not repeated.
- `chunk_layouts` skips an item that no remaining chunk holds whole, instead of blocking the items after it.
- A cached chunk manifest holding a piece exactly the cap long with table lines in it can only come from the old
  cut; it is re-chunked once, with a log line. An old prose split keeps its cached chunks.

## Consequences

- Live, the workbook above yields 280 of 280 row records (75 before), and every span is one row.
- A document with a section over the cap gets different chunk boundaries, so different `chunk_id`s and spans when
  re-ingested; the `chunk_id` scheme is unchanged and documents under the cap are untouched. A repeated header
  appears once per continuation chunk in the canonical document text.
- Whether a pack may build on the default segmenter (PS-61) and whether a record table's lost rows are reported
  (PS-62) are separate tasks.
