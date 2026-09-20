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


def test_extraction_model_carries_profile_provider_pin():
    # ADR-0100: the extraction surface honors the model PROFILE's provider routing, so the Qwen3.8-27b
    # deepinfra/bf16 pin holds engine-wide (not just the seam). An un-pinned model carries no routing.
    m = default_extraction_model("clause-extract", profiles._PRODUCT_LLM)
    assert m.provider_routing == {"only": ["deepinfra/bf16"], "allow_fallbacks": False}
    # an un-pinned model (no `provider` in its profile) carries no routing -> the _call_api sort default applies
    g = default_extraction_model("clause-extract", "ibm-granite/granite-4.1-8b")
    assert g.provider_routing is None


def test_default_extraction_model_routes_to_vllm(monkeypatch):
    # Post ADR-0100 the product default (`_PRODUCT_LLM`) is a backend-PINNED string (OpenRouter), so the
    # RAG_SERVING fallback is exercised by an UN-PINNED string. An un-pinned id (granite) still follows
    # RAG_SERVING=vllm -> the extraction model routes to the self-hosted server, back-compat with pre-0100.
    monkeypatch.setenv("RAG_SERVING", "vllm")
    monkeypatch.setenv("VLLM_BASE_URL", "https://app.modal.run/v1")
    monkeypatch.setenv("VLLM_API_KEY", "vk")
    m = default_extraction_model("clause-extract", "ibm-granite/granite-4.2-8b")
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
