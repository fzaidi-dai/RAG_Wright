"""The engine API layer (ADR-0117): the stable, domain-agnostic surface a product builds on.

EP-API-1 ships the typed config + the opaque workspace handle. Future increments add the per-kind invokers
(EP-API-2), generic `kg_read`/`kg_write` + id/format accessors (EP-API-3), and the options catalog + pluggable
embedders (EP-API-4). The product imports from here; it never reaches the store/embedder/model implementations."""
from __future__ import annotations

from rag_wright.api.config import EngineConfig, StoreConfig
from rag_wright.api.documents import aparse_document, parse_document, source_document
from rag_wright.api.ids import decode_bbox, document_of, id_source
from rag_wright.api.invoke import ainvoke_subgraph, capability_index, invoke_model
from rag_wright.api.kg import kg_read, kg_write, span_positions
from rag_wright.api.usage import ModelUsage, UsageTotals, measure_usage
from rag_wright.api.workspace import WorkspaceHandle, open_workspace

__all__ = [
    "EngineConfig", "StoreConfig", "WorkspaceHandle", "open_workspace",
    "ainvoke_subgraph", "invoke_model", "capability_index",
    "kg_read", "kg_write", "span_positions",
    "document_of", "id_source", "decode_bbox",
    "source_document", "parse_document", "aparse_document",
    "measure_usage", "UsageTotals", "ModelUsage",
]
