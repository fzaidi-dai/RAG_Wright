"""T17: RLM chunking (deterministic, content-hash gated) — FR-I.1, RAC-17.

Chunk boundaries and chunk_ids are code-deterministic (derived from the parsed document's section
structure plus the token cap), so identical input yields identical boundaries and ids across runs.
Summaries come from a `Summarizer` seam; hermetic tests inject a deterministic call-counting stub
(the real seam summarizer, structured-output/temperature-zero, is exercised opt-in `-m model`).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from docling_core.types.doc.document import DoclingDocument

from rag_wright.capabilities.parsing import ParsedDocument, parse
from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.capabilities.rlm_chunking import (
    Chunk,
    ChunkManifest,
    chunk,
    register_rlm_chunking,
)


def _two_section_doc() -> DoclingDocument:
    doc = DoclingDocument(name="stub")
    doc.add_text(label="section_header", text="1. Definitions")
    doc.add_text(label="text", text="Affiliate means an entity controlling a party.")
    doc.add_text(label="section_header", text="2. Governing Law")
    doc.add_text(label="text", text="This Agreement is governed by the laws of Delaware.")
    return doc


class _StubParser:
    def __init__(self, doc: DoclingDocument) -> None:
        self._doc = doc

    def convert(self, source: Path) -> DoclingDocument:
        return self._doc


class _StubSummarizer:
    """Deterministic summarizer that counts calls (to prove the content-hash gate)."""

    def __init__(self) -> None:
        self.calls = 0

    def summarize(self, text: str) -> str:
        self.calls += 1
        return f"summary: {text[:24]}"


def _parsed(tmp_path: Path, doc: DoclingDocument, content: bytes = b"%PDF one") -> ParsedDocument:
    src = tmp_path / "contract_a.pdf"
    src.write_bytes(content)
    return parse(src, cache_dir=tmp_path / "parsed", parser=_StubParser(doc))


def test_chunks_split_at_section_boundaries_with_summaries(tmp_path):
    parsed = _parsed(tmp_path, _two_section_doc())

    manifest = chunk(parsed, summarizer=_StubSummarizer(), cache_dir=tmp_path / "chunks")

    assert isinstance(manifest, ChunkManifest)
    assert manifest.source_doc_id == "contract_a"
    assert len(manifest.chunks) == 2  # one chunk per section
    assert [c.chunk_index for c in manifest.chunks] == [0, 1]
    assert all(isinstance(c, Chunk) and c.summary.startswith("summary:") for c in manifest.chunks)
    assert "Governing Law" in manifest.chunks[1].text


def test_chunk_ids_are_stable_and_deterministic_across_runs(tmp_path):
    parsed = _parsed(tmp_path, _two_section_doc())

    first = chunk(parsed, summarizer=_StubSummarizer(), cache_dir=tmp_path / "a")
    second = chunk(parsed, summarizer=_StubSummarizer(), cache_dir=tmp_path / "b")

    assert [c.chunk_id for c in first.chunks] == [c.chunk_id for c in second.chunks]
    assert first.model_dump() == second.model_dump()  # identical boundaries + ids + summaries
    # chunk_id is the canonical ChunkId form: <source_doc_id>:<index>:<64-hex>
    sid, idx, h = first.chunks[0].chunk_id.split(":")
    assert sid == "contract_a" and idx == "0" and len(h) == 64


def test_content_hash_gate_skips_rechunk_of_unchanged_document(tmp_path):
    parsed = _parsed(tmp_path, _two_section_doc())
    summarizer = _StubSummarizer()

    chunk(parsed, summarizer=summarizer, cache_dir=tmp_path / "chunks")
    calls_after_first = summarizer.calls
    chunk(parsed, summarizer=summarizer, cache_dir=tmp_path / "chunks")  # unchanged -> cache hit

    assert calls_after_first == 2  # one summary per section on the first run
    assert summarizer.calls == 2  # the second run re-chunks nothing (no new summarize calls)


def test_token_cap_hard_splits_oversized_content(tmp_path):
    doc = DoclingDocument(name="big")
    doc.add_text(label="text", text="word " * 400)  # ~2000 chars -> ~500 token estimate
    parsed = _parsed(tmp_path, doc)

    manifest = chunk(parsed, summarizer=_StubSummarizer(), cache_dir=tmp_path / "chunks", token_cap=100)

    assert len(manifest.chunks) >= 2  # split under the cap
    assert all(c.token_estimate <= 100 for c in manifest.chunks)  # boundary validation holds
    assert manifest.token_cap == 100


def test_accumulated_items_never_exceed_the_cap(tmp_path):
    # many medium items with no headers accumulate near the cap; the joined chunk must still honor it
    # (regression: per-item token estimates summed below the cap while the joined text exceeded it).
    doc = DoclingDocument(name="many")
    for _ in range(30):
        doc.add_text(label="text", text="clause " * 30)  # ~210 chars -> ~52 token estimate each
    parsed = _parsed(tmp_path, doc)

    manifest = chunk(parsed, summarizer=_StubSummarizer(), cache_dir=tmp_path / "chunks", token_cap=100)

    assert len(manifest.chunks) >= 2
    assert all(c.token_estimate <= 100 for c in manifest.chunks)  # no chunk slips over the cap


def test_rlm_chunking_registers_as_an_agent_skill(tmp_path):
    reg = CapabilityRegistry()
    register_rlm_chunking(reg)
    registration = reg.get("rlm_chunking")
    assert registration.kind == "agent_skill"
    assert registration.contract is ChunkManifest
    assert registration.skeleton.response_bounds is None  # agent_skill is loaded, not callable


@pytest.mark.model
def test_real_seam_summarizer_produces_a_summary():
    from rag_wright.capabilities.rlm_chunking import SeamSummarizer

    summary = SeamSummarizer().summarize(
        "This Agreement is entered into by Acme Corporation and governed by Delaware law. "
        "It defines exclusivity, term, and termination."
    )
    assert isinstance(summary, str) and summary.strip()


# --- full pipeline over a real document with real per-chunk LLM summaries (opt-in: parse + model) --


@pytest.mark.parse
@pytest.mark.model
def test_end_to_end_real_document_chunks_with_real_summaries(tmp_path):
    """Parse a real CUAD contract (Docling) and chunk it with the real seam summarizer.

    This exercises the whole RLM chunking path with live LLM calls (one per chunk) and is the level
    at which the cap-accumulation bug surfaced; hermetic stubs did not reach it.
    """
    from rag_wright.capabilities.parsing import DoclingParser, parse
    from rag_wright.capabilities.rlm_chunking import SeamSummarizer, chunk

    pdf_dir = Path("data/cuad/subset/pdf")
    scanned_json = Path("data/cuad/subset/scanned.json")
    if not scanned_json.exists():
        pytest.skip("CUAD subset not present")
    scanned = set(json.loads(scanned_json.read_text())["contract_ids"])
    text_pdfs = sorted(p for p in pdf_dir.glob("*.pdf") if p.stem not in scanned)
    if not text_pdfs:
        pytest.skip("CUAD subset PDFs not present")
    pdf = min(text_pdfs, key=lambda p: p.stat().st_size)  # smallest, to bound live-call count

    parsed = parse(pdf, cache_dir=tmp_path / "parsed", parser=DoclingParser())
    manifest = chunk(parsed, summarizer=SeamSummarizer(), cache_dir=tmp_path / "chunks", token_cap=2000)

    assert manifest.chunks
    assert all(c.token_estimate <= 2000 for c in manifest.chunks)  # boundary validation holds live
    assert all(c.summary.strip() for c in manifest.chunks)  # a real summary per chunk
