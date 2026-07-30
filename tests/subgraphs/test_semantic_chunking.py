"""LG-2: the semantic_chunking GRANULAR subgraph -- hermetic (stub discoverer/summarizer, no LLM).

Reuses the same seam fixtures as test_rlm_chunking (a stubbed DoclingDocument + discoverer + summarizer) and
verifies the node pipeline end to end: chunking, the content-hash gate, retry recovery, and the
retry-exhaustion -> dead-letter compensation.
"""

from __future__ import annotations

import threading
from pathlib import Path

from docling_core.types.doc import DoclingDocument
from langgraph.types import RetryPolicy

from rag_wright.capabilities.parsing import ParsedDocument, parse
from rag_wright.capabilities.rlm_chunking import BoundarySpan
from rag_wright.subgraphs.semantic_chunking import build_semantic_chunking

_FAST_RETRY = RetryPolicy(max_attempts=3, initial_interval=0.0)  # no backoff sleeps in tests


class _StubParser:
    def __init__(self, doc: DoclingDocument) -> None:
        self._doc = doc

    def convert(self, source: Path) -> DoclingDocument:
        return self._doc


class _StubSummarizer:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls = 0

    def summarize(self, text: str) -> str:
        with self._lock:
            self.calls += 1
        return f"summary: {text[:24]}"


class _StubDiscoverer:
    def __init__(self, spans, fail_times: int = 0) -> None:
        self._spans = spans
        self._fail_times = fail_times
        self.calls = 0

    def discover(self, document):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise RuntimeError("provider blip")
        return [BoundarySpan(start_index=a, end_index=b) for a, b in self._spans]


def _two_section_doc() -> DoclingDocument:
    doc = DoclingDocument(name="stub")
    doc.add_text(label="section_header", text="1. Definitions")
    doc.add_text(label="text", text="Affiliate means an entity controlling a party. " * 30)
    doc.add_text(label="section_header", text="2. Governing Law")
    doc.add_text(label="text", text="This Agreement is governed by the laws of Delaware. " * 30)
    return doc


def _parsed(tmp_path: Path) -> ParsedDocument:
    src = tmp_path / "contract_a.pdf"
    src.write_bytes(b"%PDF one")
    return parse(src, cache_dir=tmp_path / "parsed", parser=_StubParser(_two_section_doc()))


def test_chunks_a_document_end_to_end(tmp_path):
    disc, summ = _StubDiscoverer([(0, 1), (2, 3)]), _StubSummarizer()
    graph = build_semantic_chunking(disc, summ, retry_policy=_FAST_RETRY)
    out = graph.invoke({"parsed": _parsed(tmp_path), "cache_dir": tmp_path / "chunks"})
    manifest = out["manifest"]
    assert len(manifest.chunks) == 2  # follows the discoverer's two section spans
    assert all(c.summary.startswith("summary:") for c in manifest.chunks)
    assert out.get("dead_letter") is None


def test_content_hash_gate_reuses_cached_manifest(tmp_path):
    disc, summ = _StubDiscoverer([(0, 1), (2, 3)]), _StubSummarizer()
    graph = build_semantic_chunking(disc, summ, retry_policy=_FAST_RETRY)
    parsed, cache = _parsed(tmp_path), tmp_path / "chunks"
    graph.invoke({"parsed": parsed, "cache_dir": cache})
    graph.invoke({"parsed": parsed, "cache_dir": cache})  # cache hit -> no discover/summarize
    assert disc.calls == 1 and summ.calls == 2  # second run short-circuited at the gate


def test_discover_retry_recovers_a_transient_blip(tmp_path):
    disc = _StubDiscoverer([(0, 1), (2, 3)], fail_times=1)
    graph = build_semantic_chunking(disc, _StubSummarizer(), retry_policy=_FAST_RETRY)
    out = graph.invoke({"parsed": _parsed(tmp_path), "cache_dir": tmp_path / "chunks"})
    assert out["manifest"] is not None
    assert disc.calls == 2  # failed once, retried, succeeded


def test_discover_exhaustion_dead_letters_the_document(tmp_path):
    disc = _StubDiscoverer([(0, 1), (2, 3)], fail_times=99)  # always fails
    graph = build_semantic_chunking(disc, _StubSummarizer(), retry_policy=_FAST_RETRY)
    out = graph.invoke({"parsed": _parsed(tmp_path), "cache_dir": tmp_path / "chunks"})
    assert out["dead_letter"]["reason"] == "boundary_discovery_failed"
    assert out.get("manifest") is None  # document dropped, not raised
    assert disc.calls == 3  # retried up to max_attempts, then error_handler compensated


def test_registers_as_a_subgraph():
    from rag_wright.capabilities.registry import CapabilityRegistry
    from rag_wright.subgraphs.semantic_chunking import register_semantic_chunking_subgraph

    reg = CapabilityRegistry()
    register_semantic_chunking_subgraph(reg)
    assert reg.get("semantic_chunking").kind == "subgraph"
