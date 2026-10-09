"""The pack SDK: the stable building blocks a DOMAIN PACK's code builds on (PS-8b, G21).

Two declared tiers, both with the same compatibility promise (a breaking change only in a breaking release, with a
migration note):

- `rag_wright.api` -- what a PRODUCT's seam uses: config, workspaces, invokers, KG reads/writes, document parsing,
  ingestion and its hook contracts, usage/trace correlation, answer generation, pack loading.
- `rag_wright.pack_sdk` (this module) -- what a PACK's own code additionally needs to implement capabilities: the
  identifiers and provenance types, the model seam (model resolution, structured output, streaming), the LangGraph
  scaffold, the store protocol, the generic capabilities a pack composes (explicit-model generation and judging,
  entity resolution, reranking, embedding, parsing, chunking, the ingestion stages), and capability plumbing.

A pack imports from `rag_wright.api` and `rag_wright.pack_sdk` (and its own modules) only; anything else in the
engine is internal and may change. Everything here is a re-export of the engine's own object (no copies). Where a
name would clash with `rag_wright.api` (which takes a workspace), the explicit form is renamed here:
`agenerate_answer_with_model`, `ajudge_spans_with_judge`, `GraphExtractor`, `parse_docling_bytes` /
`aparse_docling_bytes`.
"""
from __future__ import annotations

from rag_wright.capabilities.answer_generator import agenerate_answer as agenerate_answer_with_model
from rag_wright.capabilities.answer_generator import answer_model_for
from rag_wright.capabilities.ard import ResponseBounds
from rag_wright.capabilities.disambiguation import disambiguate
from rag_wright.capabilities.document_parse import (
    INGEST_PARSE_DEADLINE_S,
    SourceDocument,
    aparsed_source_document,
    parsed_source_document,
    parsed_text_document,
)
from rag_wright.capabilities.document_scope import UnknownDocumentError, validate_documents
from rag_wright.capabilities.embedding_profiles import build_ingest_embedder
from rag_wright.capabilities.entity_resolution import ResolutionResult, resolve_entities
from rag_wright.capabilities.graph_query import GraphAnswer, graph_query
from rag_wright.capabilities.graph_storage import to_graph
from rag_wright.capabilities.invoke import capability_impl
from rag_wright.capabilities.manifests import RLM_GRANTED_SUBAGENTS
from rag_wright.capabilities.parsing import TieredOCRParser
from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.capabilities.remote_encoders import post_json, query_embedder, stack_url
from rag_wright.capabilities.reranking import BGEReranker
from rag_wright.capabilities.retrieval_core import cosine, typed_constraint_match_rank
from rag_wright.capabilities.rlm_chunking import StructuralModelFallbackDiscoverer, achunk_texts
from rag_wright.capabilities.span_relevance_judgment import ajudge_spans as ajudge_spans_with_judge
from rag_wright.capabilities.span_relevance_judgment import build_arelevance_judge_fn, finalize_verdict
from rag_wright.contracts.extraction import EntityMention, ExtractionResult, run_extractors
from rag_wright.contracts.extraction import Extractor as GraphExtractor
from rag_wright.contracts.graph import EntityNode, RelationshipFact
from rag_wright.contracts.identifiers import ChunkId, EntityId, canonical_source_doc_id
from rag_wright.contracts.provenance import ConfidenceTag, GraphFact, Provenance
from rag_wright.contracts.span import to_span_record
from rag_wright.corpus.canonicalize import EntityRules
from rag_wright.corpus.document_parser import (
    LEADING_ENUM,
    document_to_sections,
    is_bare_heading,
    section_number,
)
from rag_wright.corpus.document_parser import aparse_document_bytes as aparse_docling_bytes
from rag_wright.corpus.document_parser import parse_document_bytes as parse_docling_bytes
from rag_wright.ingestion.builder import IngestionStages
from rag_wright.models.profiles import DEFAULT_GENERAL, decision_profile, model_for, profile_for
from rag_wright.models.seam import (
    ModelCallTimeout,
    astream_text,
    build_model,
    build_structured,
    call_description,
    model_deadline_s,
    resolve_connection,
)
from rag_wright.models.tag_structured import build_tag_structured, field_kind
from rag_wright.models.tracing import finish_generation, start_generation, tracing_on
from rag_wright.models.usage import usage_capturing
from rag_wright.models.weights import models_dir
from rag_wright.ontology.pack_schema import KgVertexType, load_kg_schema
from rag_wright.ontology.registry import EntityRegistry, RegistryRecord
from rag_wright.store.arcadedb import SPAN_TYPE
from rag_wright.store.seam import Store
from rag_wright.subgraphs.scaffold import DEFAULT_RETRY, TransientExtraction, business_span, dead_letter, raw_llm_span
from rag_wright.util.concurrent import map_concurrent

__all__ = [
    # identifiers, provenance, graph + extraction contracts
    "ChunkId", "EntityId", "canonical_source_doc_id", "ConfidenceTag", "GraphFact", "Provenance",
    "EntityMention", "ExtractionResult", "GraphExtractor", "run_extractors", "EntityNode", "RelationshipFact",
    "to_span_record",
    # the store protocol
    "Store", "SPAN_TYPE",
    # the model seam + its observability
    "model_for", "profile_for", "decision_profile", "DEFAULT_GENERAL", "build_model", "build_structured",
    "build_tag_structured", "field_kind", "astream_text", "resolve_connection", "ModelCallTimeout",
    "model_deadline_s", "call_description", "start_generation", "finish_generation", "tracing_on", "usage_capturing",
    "models_dir",
    # the LangGraph scaffold
    "DEFAULT_RETRY", "TransientExtraction", "business_span", "dead_letter", "raw_llm_span",
    # generic capabilities a pack composes
    "agenerate_answer_with_model", "answer_model_for", "ajudge_spans_with_judge", "build_arelevance_judge_fn",
    "finalize_verdict", "disambiguate", "resolve_entities", "ResolutionResult", "EntityRules", "EntityRegistry",
    "RegistryRecord", "to_graph", "graph_query", "GraphAnswer", "BGEReranker", "query_embedder",
    "build_ingest_embedder", "typed_constraint_match_rank", "cosine", "post_json", "stack_url",
    # documents, parsing and chunking
    "SourceDocument", "parsed_source_document", "aparsed_source_document", "parsed_text_document",
    "INGEST_PARSE_DEADLINE_S", "TieredOCRParser", "parse_docling_bytes", "aparse_docling_bytes",
    "document_to_sections", "LEADING_ENUM", "is_bare_heading", "section_number", "StructuralModelFallbackDiscoverer",
    "achunk_texts", "IngestionStages", "UnknownDocumentError", "validate_documents",
    # capability plumbing
    "capability_impl", "CapabilityRegistry", "ResponseBounds", "RLM_GRANTED_SUBAGENTS",
    # pack schema + concurrency
    "KgVertexType", "load_kg_schema", "map_concurrent",
]
