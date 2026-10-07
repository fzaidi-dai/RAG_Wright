"""The engine's generic ingestion mechanism (ADR-0124): domain-neutral default hooks, and (ING-4) the builder."""
from __future__ import annotations

from rag_wright.ingestion.group import DEFAULT_MAX_UNIT_CHARS, group_units
from rag_wright.ingestion.layout import chunk_layouts, layout_kind, text_layout
from rag_wright.ingestion.segment import segment_layout

__all__ = ["DEFAULT_MAX_UNIT_CHARS", "chunk_layouts", "group_units", "layout_kind", "segment_layout", "text_layout"]
