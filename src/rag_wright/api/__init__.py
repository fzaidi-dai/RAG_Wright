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
    CapabilityManifest,
    engine_capabilities,
    load_pack,
    load_reference_pack,
    reference_pack,
    register_capability,
)
from rag_wright.capabilities.registry import canonical_capability_slugs, register_canonical_slugs
from rag_wright.api.answers import (AnswerKind, Condition, EvidenceItem, GeneratedAnswer, Relevance,
                                    RelevanceVerdict, agenerate_answer, ajudge_spans)
from rag_wright.api.documents import (aparse_document, aparse_document_bytes, parse_document,
                                      parse_document_bytes, source_document)
from rag_wright.api.ids import decode_bbox, document_of
from rag_wright.api.discover import Discovered, discover
from rag_wright.contracts.ingestion import (
    BoundaryDecider,
    Extractor,
    IdentifierRule,
    IngestionContractError,
    IngestionTuning,
    IngestSource,
    LayoutItem,
    LayoutKind,
    RecordTableRule,
    RecordWriter,
    Segmenter,
    Span,
    SpanKind,
    SpanTagger,
    TableMode,
    TableRow,
    TaggedSpan,
    Unit,
    UnitExtraction,
    UnitGrouper,
    UnitRepresentative,
    check_extraction,
    check_tiling,
    check_units,
)
from rag_wright.ingestion.builder import (
    DocumentHook,
    DocumentReport,
    IngestionPipeline,
    IngestionReport,
    build_ingestion,
    default_chunk_discoverer,
)
from rag_wright.capabilities.rlm_chunking import BoundaryDiscoverer
from rag_wright.ingestion.evaluate import DocumentEvaluation, IngestionEvaluation, LabelEvaluation, evaluate_ingestion
from rag_wright.ingestion.tables import table_rows
from rag_wright.store.seam import NOT_NULL, KgEdge, KgNode
from rag_wright.api.invoke import ainvoke_model, ainvoke_subgraph, capability_index, invoke_model
from rag_wright.api.kg import (entities_by_name, kg_count, kg_delete, kg_edges, kg_read, kg_update, kg_write,
                               span_positions)
from rag_wright.api.tracing import traced_run, traced_step
from rag_wright.api.usage import ModelUsage, UsageTotals, measure_usage, record_usage
from rag_wright.models.profiles import ModelRole
from rag_wright.api.workspace import WorkspaceHandle, open_workspace, pack_store, use_workspace_models

__all__ = [
    "EngineConfig", "StoreConfig", "EngineOptions", "IngestOptions", "WorkspaceHandle", "open_workspace", "pack_store", "use_workspace_models",
    "ModelRole",  # PS-1 (G17): WorkspaceHandle.model_id(role) and EngineConfig.models need it
    "ainvoke_subgraph", "invoke_model", "ainvoke_model", "capability_index", "discover", "Discovered",
    "kg_read", "kg_write", "kg_edges", "kg_count", "kg_delete", "kg_update", "entities_by_name", "span_positions", "KgNode", "KgEdge", "NOT_NULL",
    "document_of", "decode_bbox",
    "source_document", "parse_document", "aparse_document", "parse_document_bytes", "aparse_document_bytes",
    "agenerate_answer", "ajudge_spans", "EvidenceItem", "GeneratedAnswer", "AnswerKind",
    "RelevanceVerdict", "Relevance", "Condition",
    "measure_usage", "record_usage", "UsageTotals", "ModelUsage", "traced_run", "traced_step",
    "register_capability", "load_reference_pack", "reference_pack",
    # G5: a product authors + loads its own pack through the API (no engine internals)
    "CapabilityManifest", "load_pack", "engine_capabilities", "register_canonical_slugs", "canonical_capability_slugs",
    # ING-1 (ADR-0124): the ingestion hook contracts + the engine-enforced checks
    "LayoutItem", "LayoutKind", "Span", "SpanKind", "TaggedSpan", "Unit", "UnitExtraction", "IngestionContractError",
    "Segmenter", "SpanTagger", "UnitGrouper", "UnitRepresentative", "BoundaryDecider", "Extractor", "RecordWriter",
    "check_tiling", "check_units", "check_extraction",
    # ING-4b: tuning + sources
    "IngestionTuning", "RecordTableRule", "IdentifierRule", "IngestSource", "TableMode",
    "build_ingestion", "default_chunk_discoverer", "BoundaryDiscoverer", "IngestionPipeline", "DocumentHook",
    "IngestionReport", "DocumentReport",
    "evaluate_ingestion", "IngestionEvaluation", "DocumentEvaluation", "LabelEvaluation",
    "table_rows", "TableRow",
]
