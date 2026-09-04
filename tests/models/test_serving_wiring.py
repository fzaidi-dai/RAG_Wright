"""MS1-3 (ADR-0039): the production ingestion is fully vLLM-routable + the semantic judge runs inline.
Three LLM surfaces flip together via RAG_SERVING: the chunker + judge (through the seam) and clause
extraction (docling-graph's own ExtractionModel). Hermetic -- no model, no network."""

from __future__ import annotations

import pytest

from rag_wright.capabilities.dg_extraction import default_extraction_model
from rag_wright.models import profiles
from rag_wright.capabilities.rlm_chunking import SingleCallBoundaryDiscoverer
from rag_wright.contracts.identifiers import ChunkId
from rag_wright.contracts.provenance import ConfidenceTag
from rag_wright.ontology.clause_template import Clause, Mutuality
from rag_wright.spans.clause_kg_extractor import DGClausePropertyExtractor
from rag_wright.spans.semantic_judge import SemanticVerdict

_D = "mutuality"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.delenv("RAG_SERVING", raising=False)
    monkeypatch.delenv("VLLM_BASE_URL", raising=False)


# --- (extract) the docling-graph extraction model is serving-aware, like the seam ---

def test_default_extraction_model_is_openrouter_by_default():
    m = default_extraction_model("clause-extract", profiles._PRODUCT_LLM)
    assert m.provider == "openrouter" and "openrouter" in m.base_url


def test_default_extraction_model_routes_to_vllm(monkeypatch):
    monkeypatch.setenv("RAG_SERVING", "vllm")
    monkeypatch.setenv("VLLM_BASE_URL", "https://app.modal.run/v1")
    monkeypatch.setenv("VLLM_API_KEY", "vk")
    m = default_extraction_model("clause-extract", profiles._PRODUCT_LLM)
    assert m.provider == "hosted_vllm" and m.base_url == "https://app.modal.run/v1" and m.api_key == "vk"


# --- (chunk) the boundary discoverer defaults to the product LLM via the GENERAL role ---

def test_single_call_chunker_defaults_to_product_llm():
    assert SingleCallBoundaryDiscoverer()._model_id == profiles._PRODUCT_LLM


# --- (judge) the extractor applies the injected semantic judge after the deterministic gates ---

def _extractor(judge_fn):
    clause = Clause(has_mutuality=Mutuality.MUTUAL)  # -> a MUTUALITY=mutual assertion (a SEMANTIC dim)
    return DGClausePropertyExtractor(lambda _t: clause, semantic_judge_fn=judge_fn)


def _mutuality(rec):
    return next(a for a in rec.assertions if a.dimension.value == _D)


def test_injected_judge_downgrades_a_refuted_semantic_reading():
    rec = _extractor(lambda dim, value, text: SemanticVerdict(supported=False))(
        chunk_id=ChunkId.of("d", 0, "x"), function="Cap On Liability", text="only one party indemnifies")
    assert _mutuality(rec).confidence == ConfidenceTag.AMBIGUOUS


def test_no_judge_leaves_the_extractor_deterministic_only():
    rec = _extractor(None)(
        chunk_id=ChunkId.of("d", 0, "x"), function="Cap On Liability", text="each party indemnifies the other")
    assert _mutuality(rec).confidence == ConfidenceTag.EXTRACTED
