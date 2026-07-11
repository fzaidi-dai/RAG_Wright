"""T17: RLM chunking (deterministic, content-hash gated) — FR-I.1, RAC-17.

Chunk boundaries and chunk_ids are code-deterministic (derived from the parsed document's section
structure plus the token cap), so identical input yields identical boundaries and ids across runs.
Summaries come from a `Summarizer` seam; hermetic tests inject a deterministic call-counting stub
(the real seam summarizer, structured-output/temperature-zero, is exercised opt-in `-m model`).
"""

from __future__ import annotations

import asyncio
import glob
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from docling_core.types.doc.document import DoclingDocument

from rag_wright.capabilities.parsing import ParsedDocument, parse
from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.capabilities.rlm_chunking import (
    DEFAULT_TOKEN_CAP,
    MIN_CHUNK_CHARS,
    BoundaryValidationError,
    Chunk,
    ChunkManifest,
    _MIN_NONEMPTY_CHARS,
    _split_into_chunks,
    _summarize_all,
    _validate_boundaries,
    chunk,
    register_rlm_chunking,
)
from rag_wright.contracts.identifiers import ChunkId


def _two_section_doc() -> DoclingDocument:
    # each section body is over the MIN_CHUNK_CHARS floor, so the two sections stay as two chunks (a
    # major boundary with enough content splits); tiny sections would correctly merge (T-CHK).
    doc = DoclingDocument(name="stub")
    doc.add_text(label="section_header", text="1. Definitions")
    doc.add_text(label="text", text="Affiliate means an entity controlling a party. " * 30)
    doc.add_text(label="section_header", text="2. Governing Law")
    doc.add_text(label="text", text="This Agreement is governed by the laws of Delaware. " * 30)
    return doc


class _StubParser:
    def __init__(self, doc: DoclingDocument) -> None:
        self._doc = doc

    def convert(self, source: Path) -> DoclingDocument:
        return self._doc


class _StubSummarizer:
    """Deterministic summarizer that counts calls (to prove the content-hash gate). Thread-safe:
    summaries now run concurrently through asyncio.to_thread, so the counter needs a lock."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls = 0

    def summarize(self, text: str) -> str:
        with self._lock:
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


# --- T-CHK: degenerate-split fix (min-size floor + hierarchy-preserving merge) -------------------


def _item(text: str, label: str = "text", level: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(text=text, label=label, level=level)


def _doc(items: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(texts=items)  # _split_into_chunks only reads .texts/.text/.label/.level


def test_tiny_adjacent_sections_merge_up_to_the_floor():
    # 40 short same-level sections must not become 40 tiny chunks; they fold up to the floor.
    items: list[SimpleNamespace] = []
    for i in range(40):
        items.append(_item(f"{i}. Clause", "section_header", level=1))
        items.append(_item("A short clause sentence of moderate length. " * 3))  # ~132 chars
    chunks = _split_into_chunks(_doc(items), DEFAULT_TOKEN_CAP)

    assert 2 <= len(chunks) < 40  # merged, not one fragment per heading
    assert all(len(c) >= MIN_CHUNK_CHARS for c in chunks)  # every chunk meets the size floor
    assert min(len(c) for c in chunks) >= _MIN_NONEMPTY_CHARS  # no near-empty chunk


def test_deeper_subsection_stays_with_its_parent_section():
    items = [
        _item("1. Main Section", "section_header", level=1),
        _item("Body of the main section. " * 60),  # ~1600 chars, over the floor
        _item("1.1 Subsection", "section_header", level=2),  # deeper level -> not a major boundary
        _item("Subsection body text here."),
    ]
    chunks = _split_into_chunks(_doc(items), DEFAULT_TOKEN_CAP)

    assert len(chunks) == 1  # the subsection is NOT split away from its parent (hierarchy preserved)
    assert "1.1 Subsection" in chunks[0]


def test_major_boundary_splits_when_both_sections_meet_the_floor():
    big = "Clause text of the section. " * 60  # ~1680 chars, over the floor
    items = [
        _item("1. First", "section_header", level=1), _item(big),
        _item("2. Second", "section_header", level=1), _item(big),  # same level -> major boundary
    ]
    chunks = _split_into_chunks(_doc(items), DEFAULT_TOKEN_CAP)

    assert len(chunks) == 2  # a real section break splits when there is enough content on both sides


def test_asiandragon_dense_header_pathology_regression():
    # Models the real ASIANDRAGON parse: 112 level-1 headings (Docling flattened every numbered clause
    # to a level-1 header), some heading-only runs. The buggy splitter made 113 chunks (10 near-empty);
    # the floor + hierarchy-preserving merge must collapse this and emit no near-empty chunk.
    items: list[SimpleNamespace] = []
    for i in range(112):
        items.append(_item(f"{i}. HEADING", "section_header", level=1))
        if i % 5:  # ~1 in 5 headings has no body (consecutive headers) -> the near-empty source
            items.append(_item("Some clause body text of moderate length here. " * 2))  # ~94 chars
    chunks = _split_into_chunks(_doc(items), DEFAULT_TOKEN_CAP)
    lens = sorted(len(c) for c in chunks)

    assert len(chunks) < 40  # was 113 chunks on the real doc
    assert lens[0] >= _MIN_NONEMPTY_CHARS  # no near-empty / heading-only chunk (was 10)
    assert all(length >= MIN_CHUNK_CHARS for length in lens)  # the size floor holds (cap >> floor)


def test_validate_rejects_near_empty_chunk_but_allows_a_lone_short_chunk():
    ok = Chunk(chunk_id=ChunkId.of("d", 0, "a").value, chunk_index=0,
               text="A real chunk body, long enough to matter.", summary="s", token_estimate=10)
    heading_only = Chunk(chunk_id=ChunkId.of("d", 1, "b").value, chunk_index=1,
                         text="1.", summary="s", token_estimate=1)
    with pytest.raises(BoundaryValidationError, match="near-empty"):
        _validate_boundaries([ok, heading_only], DEFAULT_TOKEN_CAP)

    lone_short = Chunk(chunk_id=ChunkId.of("d", 0, "c").value, chunk_index=0,
                       text="Tiny doc.", summary="s", token_estimate=2)
    _validate_boundaries([lone_short], DEFAULT_TOKEN_CAP)  # a short single-chunk doc is allowed


def test_asiandragon_real_parse_no_longer_fragments():
    files = glob.glob("data/gate2_cache/parsed/ASIANDRAGON*.json")
    if not files:
        pytest.skip("ASIANDRAGON cached parse not present (run the GATE-2 harness to produce it)")
    doc = DoclingDocument.load_from_json(files[0])
    chunks = _split_into_chunks(doc, DEFAULT_TOKEN_CAP)
    lens = sorted(len(c) for c in chunks)

    assert len(chunks) < 60  # was 113
    assert lens[0] >= _MIN_NONEMPTY_CHARS  # was 10 near-empty chunks
    print(f"\nASIANDRAGON real: {len(chunks)} chunks (was 113); "
          f"char min={lens[0]} median={lens[len(lens) // 2]} max={lens[-1]}")


# --- concurrent summarization (async + semaphore backpressure, T19 pattern) ---------------------


def test_summaries_run_concurrently_bounded_by_the_semaphore():
    class _Probe:
        """Records the peak number of summarize() calls in flight at once."""

        def __init__(self) -> None:
            self._lock = threading.Lock()
            self.inflight = 0
            self.max_inflight = 0

        def summarize(self, text: str) -> str:
            with self._lock:
                self.inflight += 1
                self.max_inflight = max(self.max_inflight, self.inflight)
            time.sleep(0.05)  # stand in for a network round-trip so calls actually overlap
            with self._lock:
                self.inflight -= 1
            return f"s:{text}"

    probe = _Probe()
    texts = [f"t{i}" for i in range(12)]

    summaries = asyncio.run(_summarize_all(texts, probe, max_concurrency=4))

    assert summaries == [f"s:t{i}" for i in range(12)]  # gather preserves order (determinism holds)
    assert probe.max_inflight == 4  # genuinely concurrent AND bounded exactly by the semaphore


def test_serial_summarizer_reaches_only_one_in_flight():
    class _Probe:
        def __init__(self) -> None:
            self._lock = threading.Lock()
            self.inflight = 0
            self.max_inflight = 0

        def summarize(self, text: str) -> str:
            with self._lock:
                self.inflight += 1
                self.max_inflight = max(self.max_inflight, self.inflight)
            time.sleep(0.02)
            with self._lock:
                self.inflight -= 1
            return text

    probe = _Probe()
    asyncio.run(_summarize_all(["a", "b", "c"], probe, max_concurrency=1))
    assert probe.max_inflight == 1  # max_concurrency=1 is strictly serial


@pytest.mark.model
def test_concurrent_summarization_is_faster_with_real_llm_calls(capsys):
    """Live: summarize real chunk texts through the real seam summarizer, concurrently vs serially.

    Proves the async+semaphore path works with actual OpenRouter/DeepSeek calls (thread-safe under
    to_thread, no throttling errors) AND actually overlaps them (concurrent wall-clock << serial),
    which the sleep-stub concurrency test cannot show.
    """
    from rag_wright.capabilities.rlm_chunking import SeamSummarizer

    summarizer = SeamSummarizer()
    texts = [
        f"Section {i}: This clause of the agreement governs {topic}. The parties agree to the "
        f"terms set out herein, which are binding and enforceable under the governing law."
        for i, topic in enumerate(
            ["exclusivity", "termination", "payment terms", "confidentiality", "indemnification",
             "governing law", "assignment", "warranties", "limitation of liability", "notices",
             "force majeure", "dispute resolution"]
        )
    ]

    t0 = time.monotonic()
    concurrent = asyncio.run(_summarize_all(texts, summarizer, max_concurrency=8))
    t_concurrent = time.monotonic() - t0

    t0 = time.monotonic()
    asyncio.run(_summarize_all(texts, summarizer, max_concurrency=1))  # serial baseline (timing only)
    t_serial = time.monotonic() - t0

    assert len(concurrent) == len(texts)
    assert all(isinstance(s, str) and s.strip() for s in concurrent)  # real summaries, no errors/None
    with capsys.disabled():
        print(f"\n[live] {len(texts)} real summaries: concurrent(8)={t_concurrent:.1f}s "
              f"serial(1)={t_serial:.1f}s  speedup={t_serial / t_concurrent:.1f}x")
    assert t_concurrent < t_serial * 0.6  # 8-way concurrency clearly overlaps the network calls


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
