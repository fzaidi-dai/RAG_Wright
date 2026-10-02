"""EP-E2E (ADR-0117): the full-stack live proof of the engine API. A product opens a workspace, ingests a real
document, and queries it -- ENTIRELY through `rag_wright.api` (open_workspace -> source_document ->
ainvoke_subgraph), with nothing reaching `ArcadeDBStore`, `query_embedder`, or a model id. Store-gated + heavy (real
ArcadeDB + the classifier fleet + extraction/answer model backend), so it runs under `-m store`."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from rag_wright.api import (
    EngineConfig,
    StoreConfig,
    ainvoke_subgraph,
    aparse_document,
    kg_read,
    measure_usage,
    open_workspace,
    source_document,
)

_DOC = (
    "Section 8. Limitation of Liability. In no event shall either party's aggregate liability arising out of or "
    "relating to this Agreement exceed the total fees paid by Customer in the twelve (12) months preceding the "
    "claim, except for breaches of confidentiality.\n\n"
    "Section 12. Governing Law. This Agreement shall be governed by the laws of the State of New York."
)


@pytest.mark.store
def test_ingest_then_query_a_document_through_the_engine_api(tmp_path):
    cfg = EngineConfig(store=StoreConfig(
        host=os.environ["ARCADEDB_HOST"], port=os.environ["ARCADEDB_PORT"],
        user=os.environ["ARCADEDB_USER"], password=os.environ["ARCADEDB_PASSWORD"],
        protocol=os.getenv("ARCADEDB_PROTOCOL", "http")))
    ws = open_workspace(cfg, corpus="ragwright_e2e_live", reset=True)

    # 1) ingest a real document -- the whole stack (chunk -> segment -> classify -> extract -> embed -> KG write)
    #    runs through the engine API; nothing reaches ArcadeDBStore/query_embedder/model ids from the caller.
    sd = source_document("ACME_MSA", text=_DOC)
    asyncio.run(ainvoke_subgraph(
        "contract_ingestion_pipeline", {"document": sd, "cache_dir": str(tmp_path)}, resources=ws))

    # 2) the ingest actually wrote the typed clause KG (full-stack proof)
    clauses = kg_read(ws, "Clause", fields=["clause_id", "function"])
    assert clauses, "ingest wrote no clauses through the API"
    assert all(c["clause_id"].startswith("ACME_MSA:") for c in clauses)  # scoped to the ingested document

    # 3) query the just-ingested document through the API -> an answer comes back end-to-end
    out = asyncio.run(ainvoke_subgraph(
        "intra_document_qa", {"contract_id": "ACME_MSA", "question": "What is the liability cap?"}, resources=ws))
    assert out is not None  # the query leg ran over the ingested data via the API


_FIXTURE = Path("tests/fixtures/table-bearing-contract.pdf")


@pytest.mark.store
@pytest.mark.skipif(not _FIXTURE.exists(), reason="PDF fixture not present")
def test_full_pipeline_with_docling_and_usage_through_the_api(tmp_path):
    """EP-E2E-2: the WHOLE stack through `rag_wright.api` on a real PDF -- docling parse -> classifiers -> LLM
    extraction (OpenRouter) -> KG write -> query -- with the model usage/cost captured by `api.measure_usage()`.
    Store-gated + needs OpenRouter creds (the default STRUCTURED_REASONING/GENERAL roles route there)."""
    cfg = EngineConfig(store=StoreConfig(
        host=os.environ["ARCADEDB_HOST"], port=os.environ["ARCADEDB_PORT"],
        user=os.environ["ARCADEDB_USER"], password=os.environ["ARCADEDB_PASSWORD"],
        protocol=os.getenv("ARCADEDB_PROTOCOL", "http")))
    ws = open_workspace(cfg, corpus="ragwright_e2e2_live", reset=True)

    # 1) docling-parse a REAL PDF through the API -> a structure-bearing SourceDocument (not the text fallback)
    sd = asyncio.run(aparse_document("TABLE_CONTRACT", _FIXTURE, cache_dir=str(tmp_path)))
    assert sd.parsed is not None

    # 2) ingest + 3) query, all under ONE caller usage scope (EP-API-5)
    with measure_usage() as usage:
        asyncio.run(ainvoke_subgraph(
            "contract_ingestion_pipeline", {"document": sd, "cache_dir": str(tmp_path)}, resources=ws))
        clauses = kg_read(ws, "Clause", fields=["clause_id", "function"])
        assert clauses, "ingest wrote no clauses through the API"
        assert all(c["clause_id"].startswith("TABLE_CONTRACT:") for c in clauses)  # scoped to the ingested doc
        out = asyncio.run(ainvoke_subgraph(
            "intra_document_qa",
            {"contract_id": "TABLE_CONTRACT", "question": "What is the Supplier's limitation of liability?"},
            resources=ws))
        assert out is not None

    # 4) the usage/cost of the LLM work (extraction + query generation) came back through the API
    assert usage.calls > 0, "no model calls were recorded through measure_usage"
    assert usage.input_tokens > 0 and usage.output_tokens > 0
    assert usage.by_model, "no per-model usage attributed"
    # OpenRouter surfaces real cost; tolerate a model that returns none (counted as calls_without_cost, not $0)
    assert usage.cost_usd > 0 or usage.calls_without_cost > 0
