"""RuleWright's request (2026-10-31): `IngestionStages.write` reported the records it was OFFERED, and a domain writer
had no way to say what landed. A writer may now return the number that landed; `None` keeps the engine's count."""
from __future__ import annotations

import asyncio

from rag_wright.api import KgNode, UnitExtraction
from rag_wright.pack_sdk import IngestionStages
from rag_wright.packs.contracts.subgraphs.contract_ingestion_pipeline import SourceDocument

_DOC = SourceDocument(source_doc_id="D1", text="text of D1")
_EXTRACTIONS = [UnitExtraction(nodes=[KgNode("Rec", "k", {"k": f"r{i}"})]) for i in range(3)]


def _stages(writer=None, store=None, tmp_path="."):
    return IngestionStages(store, extractor=None, writer=writer, cache_dir=tmp_path)


def test_the_writer_reports_what_landed(tmp_path):
    async def writer(doc_id, extractions):
        return 1  # it landed one of the three

    assert asyncio.run(_stages(writer, tmp_path=tmp_path).write(_DOC, _EXTRACTIONS)) == 1


def test_a_writer_that_returns_nothing_keeps_the_engine_count(tmp_path):
    async def writer(doc_id, extractions):
        return None

    assert asyncio.run(_stages(writer, tmp_path=tmp_path).write(_DOC, _EXTRACTIONS)) == 3


def test_the_default_writer_counts_the_nodes_its_one_transaction_landed(tmp_path):
    class _Store:
        def kg_write(self, nodes, edges=()):
            self.nodes = list(nodes)

    store = _Store()
    assert asyncio.run(_stages(store=store, tmp_path=tmp_path).write(_DOC, _EXTRACTIONS)) == 3 == len(store.nodes)
