"""PS-13 live: product metadata ingested with a document lands on its `Document` node in ArcadeDB, can be filtered
on, comes back without the store's `@props` hint, survives a re-ingest without metadata, and is updated by a
re-ingest with new values or by `kg_update`."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from rag_wright.api import (EngineConfig, IngestSource, StoreConfig, UnitExtraction, build_ingestion, kg_read, kg_update,
                            open_workspace)

from tests.ingestion.test_builder import _FakeEmbedder

pytestmark = pytest.mark.store

SHEET = Path(__file__).resolve().parents[1] / "fixtures" / "ingestion" / "textile_spec_sheet.md"


@pytest.fixture
def ws():
    handle = open_workspace(EngineConfig(store=StoreConfig.from_env()), corpus="ragwright_test_doc_metadata",
                            reset=True)
    yield handle
    handle._store.drop()


async def _no_records(unit, *, source_doc_id):  # the neutral schema has no record type; metadata needs none
    return UnitExtraction()


def _ingest(ws, tmp_path, metadata=None):
    pipe = build_ingestion(_no_records, embedder=_FakeEmbedder(), progress=lambda _: None)
    src = IngestSource(path=str(SHEET), doc_id="spec", metadata=metadata)
    report = asyncio.run(pipe.aingest(ws, [src], cache_dir=tmp_path / "cache"))
    assert report.failed == 0


def test_metadata_is_stored_filtered_and_kept_across_re_ingest(ws, tmp_path):
    _ingest(ws, tmp_path, {"owner": "team-a", "tags": ["q3", "audit"], "pages_expected": 4, "confidential": True})
    rows = kg_read(ws, "Document", where={"owner": "team-a"},
                   fields=["doc_id", "owner", "tags", "pages_expected", "confidential"])
    assert rows == [{"doc_id": "spec", "owner": "team-a", "tags": ["q3", "audit"], "pages_expected": 4,
                     "confidential": True}]
    assert kg_read(ws, "Document", where={"owner": "team-b"}) == []

    _ingest(ws, tmp_path)  # a re-ingest without metadata leaves it in place
    assert kg_read(ws, "Document", where={"doc_id": "spec"}, fields=["owner", "tags"]) == [
        {"owner": "team-a", "tags": ["q3", "audit"]}]

    _ingest(ws, tmp_path, {"owner": "team-b"})  # a re-ingest with metadata updates the keys it gives
    assert kg_read(ws, "Document", where={"doc_id": "spec"}, fields=["owner", "tags"]) == [
        {"owner": "team-b", "tags": ["q3", "audit"]}]

    assert kg_update(ws, "Document", set={"owner": "team-c"}, where={"doc_id": "spec"}) == 1
    assert kg_read(ws, "Document", where={"owner": "team-c"}, fields=["doc_id"]) == [{"doc_id": "spec"}]
