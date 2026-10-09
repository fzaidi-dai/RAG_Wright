"""PS-R5b: a product chooses the chunk-boundary rule through `build_ingestion(chunk_discoverer=...)` -- its own
`BoundaryDiscoverer`, or the engine's default with its domain guidance (`default_chunk_discoverer(guidance=...)`)."""
from __future__ import annotations

import asyncio

from rag_wright.api import BoundaryDiscoverer, build_ingestion, default_chunk_discoverer
from rag_wright.capabilities.rlm_chunking import StructuralBoundaryDiscoverer, StructuralModelFallbackDiscoverer
from tests.ingestion.test_builder import FIXTURES, _FakeEmbedder, _record_extractor, _ws


class _CountingDiscoverer(StructuralBoundaryDiscoverer):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def discover(self, document):
        self.calls += 1
        return super().discover(document)


def test_build_ingestion_uses_the_products_chunk_discoverer(tmp_path):
    disc = _CountingDiscoverer()
    assert isinstance(disc, BoundaryDiscoverer)
    pipe = build_ingestion(_record_extractor, embedder=_FakeEmbedder(), progress=lambda _l: None, chunk_discoverer=disc)
    report = asyncio.run(pipe.aingest(_ws(), [str(FIXTURES / "textile_spec_sheet.md")], cache_dir=tmp_path / "cache"))
    assert report.documents[0].dead_letter is None and disc.calls >= 1


def test_the_default_discoverer_carries_the_products_guidance():
    disc = default_chunk_discoverer("my/model", guidance="A coherent unit is one test method.")
    assert isinstance(disc, StructuralModelFallbackDiscoverer)
    assert disc._fallback._guidance == "A coherent unit is one test method." and disc._fallback._model_id == "my/model"
