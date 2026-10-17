"""LGI-5 (docs/specs/langgraph-ingestion/plan.md): a live ingest that crashes mid-run resumes from its checkpoint and
lands exactly what an uninterrupted run lands, on the real parser, the local BGE-M3 embedder and real ArcadeDB.

No model calls: the extractor is a rule and the vision model is switched off."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from rag_wright.api import (
    EngineConfig,
    IngestionTuning,
    IngestSource,
    KgNode,
    StoreConfig,
    UnitExtraction,
    build_ingestion,
    kg_read,
    temporary_workspace,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SOURCES = [IngestSource(path=str(FIXTURES / "table-bearing-contract.pdf"), doc_id="contract"),
           IngestSource(path=str(FIXTURES / "ingestion" / "textile_spec_sheet.md"), doc_id="spec"),
           IngestSource(path=str(FIXTURES / "ingestion" / "parity_db_with_reports.xlsx"), doc_id="db")]


class _Crash(BaseException):
    """Stands in for the process dying mid-run."""


def _extractor(calls: list, crash_on: set):
    async def extract(unit, *, source_doc_id):
        calls.append(source_doc_id)
        if source_doc_id in crash_on:
            crash_on.discard(source_doc_id)
            raise _Crash()
        return UnitExtraction(nodes=[KgNode("Entity", "entity_id", {
            "entity_id": f"{source_doc_id}:{unit.index}", "span_id": unit.anchor.span_id,
            "confidence": "EXTRACTED", "name": unit.text[:60]})])
    return extract


def _landed(ws) -> dict:
    return {
        "spans": sorted((r["span_id"], r["text"]) for r in kg_read(ws, "Span", fields=["span_id", "text"])),
        "entities": sorted((r["entity_id"], r["span_id"]) for r in kg_read(ws, "Entity",
                                                                            fields=["entity_id", "span_id"])),
        "documents": sorted((r["doc_id"], r["parent_doc_id"], r["sha256"])
                            for r in kg_read(ws, "Document", fields=["doc_id", "parent_doc_id", "sha256"])),
    }


@pytest.mark.store
@pytest.mark.parse
@pytest.mark.embed
def test_a_live_ingest_that_crashed_resumes_and_lands_what_a_clean_run_lands(tmp_path, monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    monkeypatch.setattr("rag_wright.capabilities.parsing._vlm_available", lambda: False)
    config = EngineConfig(store=StoreConfig.from_env())
    one_at_a_time = IngestionTuning(document_concurrency=1)

    with temporary_workspace(config, prefix="lgi5clean") as ws:
        clean = asyncio.run(build_ingestion(_extractor([], set()), progress=lambda _l: None, tuning=one_at_a_time)
                            .aingest(ws, SOURCES, cache_dir=tmp_path / "clean"))
        clean_landed = _landed(ws)
    assert clean.failed == 0 and len(clean.documents) == 5  # three sources and the workbook's two reports

    calls = []
    saver = InMemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=None))  # strict: built-in types only
    with temporary_workspace(config, prefix="lgi5resume") as ws:
        pipe = build_ingestion(_extractor(calls, {"spec"}), progress=lambda _l: None, tuning=one_at_a_time)
        with pytest.raises(_Crash):
            asyncio.run(pipe.aingest(ws, SOURCES, cache_dir=tmp_path / "resume", checkpointer=saver, run_id="r1"))
        calls.clear()
        resumed = asyncio.run(pipe.aingest(ws, SOURCES, cache_dir=tmp_path / "resume", checkpointer=saver,
                                           run_id="r1"))
        assert "contract" not in calls and "spec" in calls  # the finished document is not extracted again
        assert [d.__dict__ for d in resumed.documents] == [d.__dict__ for d in clean.documents]
        assert _landed(ws) == clean_landed
