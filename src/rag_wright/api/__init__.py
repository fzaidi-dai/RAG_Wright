"""The engine API layer (ADR-0117): the stable, domain-agnostic surface a product builds on.

EP-API-1 ships the typed config + the opaque workspace handle. Future increments add the per-kind invokers
(EP-API-2), generic `kg_read`/`kg_write` + id/format accessors (EP-API-3), and the options catalog + pluggable
embedders (EP-API-4). The product imports from here; it never reaches the store/embedder/model implementations."""
from __future__ import annotations

from rag_wright.api.config import EngineConfig, EngineOptions, IngestOptions, StoreConfig
# The capability-registration surface lives in capabilities.manifests; re-export it here (PREP-1.5) so the public
# story is uniformly "everything is rag_wright.api". The original import path keeps working — these are the same
# objects, not a fork.
from rag_wright.capabilities.manifests import (
    load_reference_pack,
    reference_pack,
    register_capability,
)
from rag_wright.api.documents import aparse_document, parse_document, source_document
from rag_wright.api.ids import decode_bbox, document_of, id_source
from rag_wright.api.discover import Discovered, discover
from rag_wright.contracts.ingestion import (
    BoundaryDecider,
    Extractor,
    IngestionContractError,
    LayoutItem,
    LayoutKind,
    RecordWriter,
    Segmenter,
    Span,
    SpanTagger,
    TaggedSpan,
    Unit,
    UnitExtraction,
    UnitGrouper,
    check_extraction,
    check_tiling,
    check_units,
)
from rag_wright.store.seam import KgEdge, KgNode
from rag_wright.api.invoke import ainvoke_model, ainvoke_subgraph, capability_index, invoke_model
from rag_wright.api.kg import entities_by_name, kg_edges, kg_read, kg_write, span_positions
from rag_wright.api.usage import ModelUsage, UsageTotals, measure_usage
from rag_wright.api.workspace import WorkspaceHandle, open_workspace

__all__ = [
    "EngineConfig", "StoreConfig", "EngineOptions", "IngestOptions", "WorkspaceHandle", "open_workspace",
    "ainvoke_subgraph", "invoke_model", "ainvoke_model", "capability_index", "discover", "Discovered",
    "kg_read", "kg_write", "kg_edges", "entities_by_name", "span_positions", "KgNode", "KgEdge",
    "document_of", "id_source", "decode_bbox",
    "source_document", "parse_document", "aparse_document",
    "measure_usage", "UsageTotals", "ModelUsage",
    "register_capability", "load_reference_pack", "reference_pack",
    # ING-1 (ADR-0124): the ingestion hook contracts + the engine-enforced checks
    "LayoutItem", "LayoutKind", "Span", "TaggedSpan", "Unit", "UnitExtraction", "IngestionContractError",
    "Segmenter", "SpanTagger", "UnitGrouper", "BoundaryDecider", "Extractor", "RecordWriter",
    "check_tiling", "check_units", "check_extraction",
]
