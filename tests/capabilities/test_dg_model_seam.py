"""GP-1B.3: the extraction-model seam. The model constructors + `build_pipeline_config` bake in the two
reliability fixes (structured_output=False; max_tokens cap) and route Gemma/DeepSeek via OpenRouter and
Granite via Ollama. Hermetic (no LLM, no docling-graph pipeline run)."""

from __future__ import annotations

import pytest

from rag_wright.capabilities.dg_extraction import (
    ExtractionModel,
    build_pipeline_config,
    ollama_model,
    openrouter_model,
)

@pytest.fixture(autouse=True)
def _no_context_probe(monkeypatch):
    # docling-graph probes the provider's /models endpoint for an unknown model's context window -- network, not here
    monkeypatch.setattr("docling_graph.llm_clients.config._probe_openai_compatible_max_model_len", lambda *a: None)



def test_openrouter_model_from_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    m = openrouter_model("deepseek", "deepseek/deepseek-v4-pro")
    assert m.provider == "openrouter" and m.model == "deepseek/deepseek-v4-pro"
    assert m.api_key == "sk-test" and "openrouter.ai" in m.base_url and m.inference == "remote"


def test_ollama_model_is_local_no_key():
    m = ollama_model("granite", "granite4:tiny", base_url="http://localhost:11434")
    assert m.provider == "ollama" and m.api_key is None and m.inference == "local"
    assert m.base_url == "http://localhost:11434"


def test_config_bakes_in_reliability_fixes(tmp_path):
    src = tmp_path / "c.md"
    src.write_text("x", encoding="utf-8")
    m = ExtractionModel("gemma", "openrouter", "google/gemma-4-31b-it",
                        "https://openrouter.ai/api/v1", "k")
    cfg = build_pipeline_config(str(src), m, max_tokens=1500)
    assert cfg.structured_output is False  # reliability fix: json_object, not strict nested json_schema
    assert cfg.provider_override == "openrouter"
    assert cfg.model_override == "google/gemma-4-31b-it"
    assert cfg.llm_overrides.generation.max_tokens == 1500  # context-window fix
    # INGEST-GRAPH-LATENCY: a sane per-call timeout, NOT docling-graph's 300s default (which let one stuck
    # extract_parties call block a document for ~5 min). Guard against the default creeping back.
    assert cfg.llm_overrides.reliability.timeout_s <= 120
    assert cfg.llm_overrides.reliability.timeout_s == 90
    assert cfg.llm_overrides.reliability.max_retries == 1
