"""RuleWright's request (2026-10-31): a write conflict lost a document's clauses. The reference pack's `write` node
consumed the pending extractions before the store write landed, so the retry of that node wrote nothing and reported
success. Hermetic: the real per-document graph from `aproduction_document_ingest`, its model stages stubbed, over a
fake store whose first clause write raises ArcadeDB's conflict."""
from __future__ import annotations

import asyncio

from rag_wright.api import KgNode, UnitExtraction
from rag_wright.pack_sdk import IngestionStages
from rag_wright.packs.contracts.subgraphs import contract_ingestion_pipeline as pipe
from rag_wright.packs.contracts.subgraphs.contract_ingestion_pipeline import SourceDocument

_CONFLICT = "Concurrent modification on page PageId(db/48/1) in file 'HAS_ASYMMETRY_0.48.65536.v0.bucket'"


class _ConflictOnceStore:
    """Raises the conflict on the first clause write (the store's own retry budget already spent), then lands."""

    def __init__(self) -> None:
        self.clauses: list[str] = []
        self._raised = False

    def kg_write(self, nodes, edges=()) -> None:
        nodes = list(nodes)
        if nodes and nodes[0].type == "Clause":
            if not self._raised:
                self._raised = True
                raise RuntimeError(_CONFLICT)
            self.clauses += [n.props["clause_id"] for n in nodes]

    def write_graph(self, nodes, edges) -> None:
        pass


class _StubStages(IngestionStages):
    """The real `write` (the pack's clause writer, then `kg_write`); every model-backed stage stubbed."""

    async def chunk(self, sd):
        return ["chunk"]

    async def segment(self, sd, chunks):
        return []

    async def index(self, sd, segments, chunks):
        return 0

    async def extract(self, sd, segments, **_kw):
        from rag_wright.ingestion.builder import ExtractStage

        clause_ids = [f"{sd.source_doc_id}:{i}" for i in range(3)]
        return ExtractStage(extractions=[UnitExtraction(nodes=[KgNode("Clause", "clause_id", {"clause_id": c})])
                                         for c in clause_ids])


class _FakeContractKG:
    def __init__(self, store) -> None:
        self._store = store

    def already_written(self, clause_id: str) -> bool:
        return clause_id in self._store.clauses

    def upsert_contract(self, record) -> None:
        pass


def _graph(monkeypatch, store, tmp_path):
    monkeypatch.setattr("rag_wright.pack_sdk.IngestionStages", _StubStages)
    monkeypatch.setattr("rag_wright.packs.contracts.capabilities.contract_kg_store.ContractKGStore", _FakeContractKG)
    monkeypatch.setattr("rag_wright.packs.contracts.spans.clause_kg_extractor.classifier_property_extractor",
                        lambda **kw: object())
    monkeypatch.setattr("rag_wright.packs.contracts.spans.model_capabilities.capability_property_classifier_fn",
                        lambda: None)
    monkeypatch.setattr("rag_wright.packs.contracts.spans.semantic_judge.select_asemantic_judge", lambda mid: None)
    monkeypatch.setattr("rag_wright.packs.contracts.spans.residual_candidates.select_residual_extractor", lambda: None)
    monkeypatch.setattr("rag_wright.packs.contracts.spans.boundary.jev_boundary_decider", lambda: None)

    async def _no_parties(text):
        return None

    monkeypatch.setattr("rag_wright.packs.contracts.capabilities.graph_extraction.aproduction_extract_fn",
                        lambda **kw: _no_parties)
    monkeypatch.setattr("rag_wright.pack_sdk.StructuralModelFallbackDiscoverer", lambda *a, **kw: None)
    return pipe.aproduction_document_ingest(
        store=store, cache_dir=str(tmp_path), registry=None, embedder=object(), classify_fn=object(),
        affiliations=False)


def test_a_retried_write_lands_every_clause(monkeypatch, tmp_path):
    store = _ConflictOnceStore()
    graph = _graph(monkeypatch, store, tmp_path)

    out = asyncio.run(graph.ainvoke({"document": SourceDocument(source_doc_id="D1", text="text of D1")}))

    assert out.get("dead_letter") is None
    assert sorted(store.clauses) == ["D1:0", "D1:1", "D1:2"]  # the retry wrote what the first attempt could not
    assert out["written"]["clauses"] == 3  # RuleWright's second request: the count is what landed, from the writer
