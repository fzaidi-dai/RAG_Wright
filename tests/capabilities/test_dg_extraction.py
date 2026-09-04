"""GP-1B.1: the docling-graph contract template. Verifies the entity/edge markers docling-graph reads
(graph_id_fields identity, PARTY_TO edge_label) and the local `edge()` helper. Hermetic (no docling-graph
pipeline, no LLM)."""

from __future__ import annotations

import logging

import pytest
from pydantic import BaseModel

from rag_wright.capabilities.dg_extraction import (
    ContractParties,
    ExtractionFailed,
    ExtractionModel,
    Party,
    capture_docling_errors,
    edge,
)


def test_entity_identity_markers():
    assert ContractParties.model_config["graph_id_fields"] == ["title"]
    assert Party.model_config["graph_id_fields"] == ["name"]


def test_parties_is_a_party_to_edge():
    jse = ContractParties.model_fields["parties"].json_schema_extra
    assert jse["edge_label"] == "PARTY_TO"


def test_edge_helper_sets_flags():
    class _T(BaseModel):
        x: list = edge("R", default_factory=list, reference=True, closed_catalog=True)

    jse = _T.model_fields["x"].json_schema_extra
    assert jse["edge_label"] == "R"
    assert jse["graph_reference"] is True
    assert jse["reference_closed_catalog"] is True


def test_instance_roundtrips():
    c = ContractParties(title="Acme-Beta Agreement",
                        parties=[Party(name="Acme Corp"), Party(name="Beta Inc")])
    assert c.title == "Acme-Beta Agreement"
    assert [p.name for p in c.parties] == ["Acme Corp", "Beta Inc"]
    assert ContractParties(title="Empty").parties == []  # default_factory=list, extract-nothing case


def test_docling_graph_accepts_the_template():
    """Lint: docling-graph's own GraphConverter turns a ContractParties instance into the expected
    entity nodes + PARTY_TO edges (no LLM) -- proves the template is a valid docling-graph template."""
    from docling_graph.core.converters.graph_converter import GraphConverter

    inst = ContractParties(title="Acme-Beta Sponsorship Agreement",
                           parties=[Party(name="Acme Corp"), Party(name="Beta Inc")])
    graph, _meta = GraphConverter().pydantic_list_to_graph([inst])
    labels = [d.get("label") for _, d in graph.nodes(data=True)]
    assert "ContractParties" in labels and labels.count("Party") == 2
    assert "PARTY_TO" in {d.get("label") for _, _, d in graph.edges(data=True)}


# --- PROD-3 lossless invariant (ADR-0050): capture docling errors -> raise ExtractionFailed, not silent-empty ---


def _model_stub():
    return ExtractionModel(label="x", provider="openrouter", model="x", base_url="http://x", api_key=None)


def test_capture_collects_docling_error_records_from_children():
    # a child logger's ERROR (like the LLM client's "Invalid JSON response") propagates up and is captured
    with capture_docling_errors() as errs:
        logging.getLogger("docling_graph.llm_clients.litellm").error("Invalid JSON response: Unterminated string")
        logging.getLogger("docling_graph").warning("just a warning")  # below ERROR -> ignored
    assert len(errs) == 1 and "Unterminated string" in errs[0]


def test_capture_is_empty_on_a_clean_run_and_detaches_after():
    with capture_docling_errors() as errs:
        logging.getLogger("docling_graph").info("Extraction OK")
    assert errs == []
    # the handler is removed on exit -> a later error is NOT captured by the old list
    logging.getLogger("docling_graph").error("after")
    assert errs == []


def test_extract_parties_raises_extractionfailed_when_docling_logs_an_error(monkeypatch):
    # docling-graph LOGS a failure then returns an EMPTY ctx (swallows it); we must RAISE, not return None
    import rag_wright.capabilities.dg_extraction as dg

    class _Ctx:
        extracted_models: list = []

    def _fake_run_pipeline(config, mode="api"):
        logging.getLogger("docling_graph.llm_clients.litellm").error("LiteLLMClient: Invalid JSON response")
        return _Ctx()

    monkeypatch.setattr(dg, "run_pipeline", _fake_run_pipeline, raising=False)
    monkeypatch.setattr("docling_graph.run_pipeline", _fake_run_pipeline, raising=False)
    with pytest.raises(ExtractionFailed) as ei:
        dg.extract_parties("some contract text", _model_stub(), stage="clause")
    assert ei.value.stage == "clause" and "Invalid JSON" in ei.value.reason


def test_extract_parties_returns_none_on_a_genuine_clean_empty(monkeypatch):
    # no error logged + no models -> a genuine empty extraction, NOT a failure -> return None (not raise)
    import rag_wright.capabilities.dg_extraction as dg

    class _Ctx:
        extracted_models: list = []

    monkeypatch.setattr("docling_graph.run_pipeline", lambda config, mode="api": _Ctx(), raising=False)
    assert dg.extract_parties("text", _model_stub()) is None



# --- EXEC-1: extraction runs on a dedicated, env-sized executor (not the CPU-derived to_thread default) ---

def test_extraction_executor_is_a_singleton_sized_by_env(monkeypatch):
    import rag_wright.capabilities.dg_extraction as dg

    monkeypatch.setenv("RAG_EXTRACT_WORKERS", "20")
    monkeypatch.setattr(dg, "_EXTRACTION_EXECUTOR", None)  # reset the lazy singleton so the env is read
    ex1 = dg.extraction_executor()
    ex2 = dg.extraction_executor()
    try:
        assert ex1 is ex2                       # singleton (reused for the process lifetime)
        assert ex1._max_workers == 20           # sized by RAG_EXTRACT_WORKERS, not min(32, cpu+4)
    finally:
        ex1.shutdown(wait=False)
        monkeypatch.setattr(dg, "_EXTRACTION_EXECUTOR", None)


async def test_aextract_parties_runs_beyond_the_default_cpu_pool_ceiling(monkeypatch):
    # EXEC-1: 24 concurrent extractions must all run at once (the dedicated 24-worker pool), which is impossible
    # on asyncio's default min(32, cpu+4) executor. Proves extraction concurrency is our knob, not a CPU default.
    import threading
    import time

    import rag_wright.capabilities.dg_extraction as dg

    monkeypatch.setenv("RAG_EXTRACT_WORKERS", "24")
    monkeypatch.setattr(dg, "_EXTRACTION_EXECUTOR", None)
    state = {"n": 0, "max": 0}
    lock = threading.Lock()

    def _probe(text, model, **kw):  # stand-in for extract_parties -> the docling-graph offload
        with lock:
            state["n"] += 1
            state["max"] = max(state["max"], state["n"])
        time.sleep(0.05)
        with lock:
            state["n"] -= 1
        return None

    monkeypatch.setattr(dg, "extract_parties", _probe)
    try:
        import asyncio
        await asyncio.gather(*(dg.aextract_parties("t", object()) for _ in range(24)))
        assert state["max"] == 24  # all 24 extractions ran concurrently -> beyond the ~16 CPU-derived default
    finally:
        dg.extraction_executor().shutdown(wait=False)
        monkeypatch.setattr(dg, "_EXTRACTION_EXECUTOR", None)
