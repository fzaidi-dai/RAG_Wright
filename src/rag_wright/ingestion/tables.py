"""ING-7 (ADR-0124): the generic table-rows primitive.

`table_rows(source_document)` returns every parsed table's data rows as exact cells, read from the parse's cell
GRID -- not from chunk text, so a table the chunker split across chunks still comes back whole, with its header.
Domain-neutral: it says what the cells ARE, never what a column MEANS (that is a domain capability).
"""
from __future__ import annotations

from typing import Any

from rag_wright.contracts.ingestion import TableRow


def _sheet_of(table: Any, document: Any) -> Any:
    """The spreadsheet sheet a table sits in (its nearest ancestor group labelled `sheet`), or None."""
    node = table
    while getattr(node, "parent", None) is not None:
        node = node.parent.resolve(document)
        if getattr(getattr(node, "label", None), "value", getattr(node, "label", None)) == "sheet":
            return getattr(node, "name", None)
    return None


def rows_of(table: Any, document: Any) -> list[TableRow]:
    """One parsed table's data rows (grid rows after the first)."""
    grid = table.data.grid
    if len(grid) < 2:
        return []
    columns = [" ".join((c.text or "").split()) for c in grid[0]]
    sheet = _sheet_of(table, document)
    page = table.prov[0].page_no if getattr(table, "prov", None) else None
    page = page if (page and not sheet) else None  # a sheet's page_no is a sheet index, not a page
    return [TableRow(table_ref=table.self_ref, sheet=sheet, page=page, row_index=i, columns=columns,
                     values=[" ".join((c.text or "").split()) for c in row])
            for i, row in enumerate(grid[1:], 1)]


def table_rows(source_document: Any) -> list[TableRow]:
    """Every data row of every table in a parsed document (`parse_document` output), in document order."""
    from rag_wright.capabilities.parsing import load_document

    if getattr(source_document, "parsed", None) is None:
        return []  # a text-only document has no parsed tables
    document = load_document(source_document.parsed)
    return [r for table in visible_tables(document) for r in rows_of(table, document)]


def visible_tables(document: Any) -> list:
    """The tables the engine ingests: body/furniture layers only -- a hidden sheet the caller chose to skip stays in
    docling's INVISIBLE layer and is left out, consistently with the rest of ingestion."""
    from docling_core.types.doc.document import ContentLayer

    return [t for t in getattr(document, "tables", []) or []
            if getattr(t, "content_layer", ContentLayer.BODY) in (ContentLayer.BODY, ContentLayer.FURNITURE)]
