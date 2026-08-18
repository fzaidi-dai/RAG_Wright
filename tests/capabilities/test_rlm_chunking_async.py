"""ASYNC-B2a (ADR-0057): the async chunk discoverer (`adiscover`) + `achunk`. Hermetic -- injected stubs, no
model or network.
"""
from __future__ import annotations

from rag_wright.capabilities.rlm_chunking import (
    BoundarySpan,
    ChunkManifest,
    SingleCallBoundaryDiscoverer,
    _BoundaryList,
    achunk,
    load_document,
)
from tests.capabilities.test_rlm_chunking import _StubSummarizer, _parsed, _two_section_doc


class _StubAsyncDiscoverer:
    def __init__(self, spans):
        self._spans = spans
        self.calls = 0

    async def adiscover(self, document):
        self.calls += 1
        return [BoundarySpan(start_index=a, end_index=b) for a, b in self._spans]


async def test_achunk_produces_a_manifest_via_the_async_discoverer(tmp_path):
    parsed = _parsed(tmp_path, _two_section_doc())
    disc = _StubAsyncDiscoverer([(0, 1), (2, 3)])
    manifest = await achunk(parsed, summarizer=_StubSummarizer(), discoverer=disc, cache_dir=tmp_path / "chunks")
    assert isinstance(manifest, ChunkManifest)
    assert len(manifest.chunks) == 2  # two sections -> two chunks
    assert disc.calls == 1


async def test_achunk_honors_the_content_hash_gate(tmp_path):
    parsed = _parsed(tmp_path, _two_section_doc())
    disc = _StubAsyncDiscoverer([(0, 1), (2, 3)])
    cache = tmp_path / "chunks"
    await achunk(parsed, summarizer=_StubSummarizer(), discoverer=disc, cache_dir=cache)
    await achunk(parsed, summarizer=_StubSummarizer(), discoverer=disc, cache_dir=cache)  # second run -> cache hit
    assert disc.calls == 1  # reused the cached manifest, no re-discover


async def test_single_call_adiscover_goes_through_the_async_seam(tmp_path):
    class _FakeAsyncStructured:
        async def ainvoke(self, _prompt):
            return _BoundaryList.model_validate({"spans": [{"start_index": 0, "end_index": 1}]})

    disc = SingleCallBoundaryDiscoverer(model_id="m", structured_factory=lambda *_a: _FakeAsyncStructured())
    document = load_document(_parsed(tmp_path, _two_section_doc()))
    spans = await disc.adiscover(document)
    assert spans and all(isinstance(s, BoundarySpan) for s in spans)  # repaired to a valid partition
