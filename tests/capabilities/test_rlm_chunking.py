"""T17: RLM chunking (LLM semantic boundary discovery) — FR-I.1, RAC-17.

Chunk boundaries are decided by an LLM exploring the document (the RLM machinery); the layer that runs
after boundaries are chosen is deterministic given those spans and lives in `_finalize_chunks` (join,
cap, T-CHK floor/merge, id, validate). Hermetic tests inject a stub `BoundaryDiscoverer` (and a
call-counting `Summarizer` stub); the real LLM discoverer and summarizer are exercised opt-in `-m model`.
Recursion is available-when-warranted, not required of chunking, so nothing here gates on it (ADR-0019).
"""

from __future__ import annotations

import asyncio
import glob
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from docling_core.types.doc.document import DoclingDocument
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from rag_wright.capabilities.parsing import ParsedDocument, parse
from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.capabilities.rlm_chunking import (
    DEFAULT_TOKEN_CAP,
    MIN_CHUNK_CHARS,
    BoundarySpan,
    BoundaryValidationError,
    Chunk,
    ChunkManifest,
    SeamBoundaryDiscoverer,
    StructuralBoundaryDiscoverer,
    _MIN_NONEMPTY_CHARS,
    _finalize_chunks,
    _summarize_all,
    _validate_boundaries,
    _validate_partition,
    canonical_document_text,
    chunk,
    register_rlm_chunking,
)
from rag_wright.contracts.identifiers import ChunkId


# --- hermetic stubs ------------------------------------------------------------------------------


class _StubDiscoverer:
    """A boundary discoverer that returns fixed spans (stands in for the LLM; counts calls)."""

    def __init__(self, spans: list[tuple[int, int]]) -> None:
        self._spans = spans
        self.calls = 0

    def discover(self, document) -> list[BoundarySpan]:
        self.calls += 1
        return [BoundarySpan(start_index=a, end_index=b) for a, b in self._spans]


class _StubSummarizer:
    """Deterministic summarizer that counts calls (to prove the content-hash gate). Thread-safe:
    summaries run concurrently through asyncio.to_thread, so the counter needs a lock."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls = 0

    def summarize(self, text: str) -> str:
        with self._lock:
            self.calls += 1
        return f"summary: {text[:24]}"


class _StubParser:
    def __init__(self, doc: DoclingDocument) -> None:
        self._doc = doc

    def convert(self, source: Path) -> DoclingDocument:
        return self._doc


def _two_section_doc() -> DoclingDocument:
    doc = DoclingDocument(name="stub")
    doc.add_text(label="section_header", text="1. Definitions")
    doc.add_text(label="text", text="Affiliate means an entity controlling a party. " * 30)
    doc.add_text(label="section_header", text="2. Governing Law")
    doc.add_text(label="text", text="This Agreement is governed by the laws of Delaware. " * 30)
    return doc


def _parsed(tmp_path: Path, doc: DoclingDocument, content: bytes = b"%PDF one") -> ParsedDocument:
    src = tmp_path / "contract_a.pdf"
    src.write_bytes(content)
    return parse(src, cache_dir=tmp_path / "parsed", parser=_StubParser(doc))


# a discoverer that returns the two section spans of `_two_section_doc` (items [0,1] and [2,3])
def _section_discoverer() -> _StubDiscoverer:
    return _StubDiscoverer([(0, 1), (2, 3)])


# --- boundaries follow the discoverer's semantic spans (not fixed-size) ---------------------------


def test_chunks_follow_the_discoverers_semantic_spans(tmp_path):
    parsed = _parsed(tmp_path, _two_section_doc())

    manifest = chunk(parsed, summarizer=_StubSummarizer(), discoverer=_section_discoverer(),
                     cache_dir=tmp_path / "chunks")

    assert isinstance(manifest, ChunkManifest)
    assert manifest.source_doc_id == "contract_a"
    assert len(manifest.chunks) == 2  # one chunk per discoverer span, not fixed-size intervals
    assert [c.chunk_index for c in manifest.chunks] == [0, 1]
    assert all(isinstance(c, Chunk) and c.summary.startswith("summary:") for c in manifest.chunks)
    # each chunk is exactly the joined text of its span's items (structural, item-aligned)
    assert "Definitions" in manifest.chunks[0].text and "Affiliate" in manifest.chunks[0].text
    assert "Governing Law" in manifest.chunks[1].text


def test_chunk_ids_are_stable_given_the_same_spans(tmp_path):
    parsed = _parsed(tmp_path, _two_section_doc())

    first = chunk(parsed, summarizer=_StubSummarizer(), discoverer=_section_discoverer(), cache_dir=tmp_path / "a")
    second = chunk(parsed, summarizer=_StubSummarizer(), discoverer=_section_discoverer(), cache_dir=tmp_path / "b")

    assert [c.chunk_id for c in first.chunks] == [c.chunk_id for c in second.chunks]
    assert first.model_dump() == second.model_dump()  # id/gate/validation deterministic given the spans
    sid, idx, h = first.chunks[0].chunk_id.split(":")
    assert sid == "contract_a" and idx == "0" and len(h) == 64


def test_content_hash_gate_skips_rechunk_and_the_llm_call(tmp_path):
    parsed = _parsed(tmp_path, _two_section_doc())
    summarizer, discoverer = _StubSummarizer(), _section_discoverer()

    chunk(parsed, summarizer=summarizer, discoverer=discoverer, cache_dir=tmp_path / "chunks")
    chunk(parsed, summarizer=summarizer, discoverer=discoverer, cache_dir=tmp_path / "chunks")  # cache hit

    assert discoverer.calls == 1  # the gate skips the (expensive) LLM boundary discovery on re-chunk
    assert summarizer.calls == 2  # two summaries once; the second run summarizes nothing


# --- CU-B1: chunk offsets slice the canonical document text byte-faithfully ------------------------


def test_chunk_offsets_slice_the_canonical_text(tmp_path):
    parsed = _parsed(tmp_path, _two_section_doc())
    manifest = chunk(parsed, summarizer=_StubSummarizer(), discoverer=_section_discoverer(),
                     cache_dir=tmp_path / "chunks")
    canonical = canonical_document_text(manifest.chunks)
    for c in manifest.chunks:  # every chunk's doc_start:doc_end slices back to its text
        assert c.doc_start is not None and c.doc_end is not None
        assert canonical[c.doc_start:c.doc_end] == c.text
    assert manifest.chunks[0].doc_start == 0
    # chunks are contiguous with exactly the separator between them
    assert manifest.chunks[1].doc_start == manifest.chunks[0].doc_end + len("\n\n")
    assert manifest.chunks[-1].doc_end == len(canonical)


# --- the deterministic-given-boundaries layer ----------------------------------------------------


def _item(text: str, label: str = "text", level: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(text=text, label=label, level=level)


def _doc(items: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(texts=items)


def _one_span_per_item(n: int) -> list[BoundarySpan]:
    return [BoundarySpan(start_index=i, end_index=i) for i in range(n)]


class _SegStubDiscoverer:
    """SEG-2 stub: returns fixed spans; counts sync + async calls."""

    def __init__(self, spans: list[tuple[int, int]]) -> None:
        self._spans = spans
        self.calls = 0
        self.acalls = 0

    def discover(self, document) -> list[BoundarySpan]:
        self.calls += 1
        return [BoundarySpan(start_index=a, end_index=b) for a, b in self._spans]

    async def adiscover(self, document) -> list[BoundarySpan]:
        self.acalls += 1
        return [BoundarySpan(start_index=a, end_index=b) for a, b in self._spans]


def test_chunk_texts_reuses_the_shared_discover_finalize_seam():
    # SEG-2: chunk_texts = the shared discover->finalize path (no cache, no summarize). Given a discoverer's
    # spans it returns the finalized chunk texts with FULL coverage (no item text lost).
    from rag_wright.capabilities.rlm_chunking import chunk_texts

    doc = _doc([_item("Alpha statement."), _item("Beta statement."), _item("Gamma statement.")])
    disc = _SegStubDiscoverer([(0, 1), (2, 2)])
    texts = chunk_texts(doc, discoverer=disc)
    assert disc.calls == 1                                       # the shared single-call-style discoverer was used
    joined = " ".join(texts)
    assert "Alpha" in joined and "Beta" in joined and "Gamma" in joined   # coverage: nothing dropped


async def test_achunk_texts_awaits_the_async_discoverer():
    from rag_wright.capabilities.rlm_chunking import achunk_texts

    doc = _doc([_item("Alpha statement."), _item("Beta statement.")])
    disc = _SegStubDiscoverer([(0, 0), (1, 1)])
    texts = await achunk_texts(doc, discoverer=disc)
    assert disc.acalls == 1 and disc.calls == 0                  # async path used adiscover
    joined = " ".join(texts)
    assert "Alpha" in joined and "Beta" in joined


def test_chunk_texts_defaults_to_the_production_ingestion_discoverer(monkeypatch):
    # SEG-5: the subject's chunker default is StructuralModelFallbackDiscoverer -- the SAME discoverer production
    # ingestion uses (contract_ingestion_pipeline). Proven by: a fully-structured, under-cap doc chunks with ZERO
    # model calls (structural boundaries), which only the structural-first discoverer can do -- single-call would
    # attempt a model call. A spy subclass records that the default was used.
    import rag_wright.capabilities.rlm_chunking as rc

    used = {"n": 0}
    real = rc.StructuralModelFallbackDiscoverer

    class _Spy(real):
        def discover(self, document):
            used["n"] += 1
            return super().discover(document)  # real structural pass: deterministic, no model for this doc

    monkeypatch.setattr(rc, "StructuralModelFallbackDiscoverer", _Spy)
    doc = _doc([_item("Section One", "section_header"), _item("The first section body sentence."),
                _item("Section Two", "section_header"), _item("The second section body sentence.")])
    texts = rc.chunk_texts(doc)  # NO discoverer, NO model configured
    assert used["n"] == 1                                              # the production ingestion default was used
    joined = " ".join(texts)
    assert "first section body" in joined and "second section body" in joined  # coverage, offline


def test_finalize_joins_span_items_and_honors_the_cap():
    doc = _doc([_item("word " * 400)])  # ~2000 chars, one item
    texts = _finalize_chunks(doc, [BoundarySpan(start_index=0, end_index=0)], token_cap=100)
    assert len(texts) >= 2  # over-cap span hard-split under the cap (cap = 100 tokens ~ 400 chars)
    assert all(len(t) <= 400 for t in texts)


def test_partition_validation_rejects_gaps_overlaps_and_short_coverage():
    _validate_partition([BoundarySpan(start_index=0, end_index=1), BoundarySpan(start_index=2, end_index=2)], 3)
    with pytest.raises(BoundaryValidationError):  # gap at index 1
        _validate_partition([BoundarySpan(start_index=0, end_index=0), BoundarySpan(start_index=2, end_index=2)], 3)
    with pytest.raises(BoundaryValidationError):  # overlap at index 1
        _validate_partition([BoundarySpan(start_index=0, end_index=1), BoundarySpan(start_index=1, end_index=2)], 3)
    with pytest.raises(BoundaryValidationError):  # does not reach the last item
        _validate_partition([BoundarySpan(start_index=0, end_index=1)], 3)


def test_over_cap_span_is_hard_split(tmp_path):
    doc = DoclingDocument(name="big")
    doc.add_text(label="text", text="word " * 400)  # ~2000 chars -> ~500 token estimate
    parsed = _parsed(tmp_path, doc)

    manifest = chunk(parsed, summarizer=_StubSummarizer(), discoverer=_StubDiscoverer([(0, 0)]),
                     cache_dir=tmp_path / "chunks", token_cap=100)

    assert len(manifest.chunks) >= 2  # split under the cap
    assert all(c.token_estimate <= 100 for c in manifest.chunks)


# --- T-CHK: the floor + near-empty rejection now fire over the DISCOVERER's spans -----------------


def test_tiny_discoverer_spans_merge_up_to_the_floor():
    # worst case: the LLM returns a boundary around every single item (headings and short bodies alone).
    # The floor must coalesce them, exactly as it did for the old mechanical splitter (T-CHK).
    items: list[SimpleNamespace] = []
    for i in range(40):
        items.append(_item(f"{i}. Clause", "section_header", level=1))
        items.append(_item("A short clause sentence of moderate length. " * 3))  # ~132 chars
    doc = _doc(items)

    # a small cap (~1500 chars) so several chunks form; without the floor these would be 80 fragments.
    texts = _finalize_chunks(doc, _one_span_per_item(len(items)), token_cap=375)

    assert 2 <= len(texts) < 40  # coalesced to a few chunks, not one fragment per heading/body
    assert all(len(t) >= MIN_CHUNK_CHARS for t in texts)  # every chunk meets the size floor
    assert min(len(t) for t in texts) >= _MIN_NONEMPTY_CHARS  # no near-empty chunk


def test_asiandragon_dense_header_pathology_over_discoverer_spans():
    # The real ASIANDRAGON shape: 112 level-1 headings, some heading-only runs. An LLM discoverer that
    # returned a span per item must not yield 113 chunks (10 near-empty); the floor collapses it (T-CHK).
    items: list[SimpleNamespace] = []
    for i in range(112):
        items.append(_item(f"{i}. HEADING", "section_header", level=1))
        if i % 5:
            items.append(_item("Some clause body text of moderate length here. " * 2))  # ~94 chars
    doc = _doc(items)

    texts = _finalize_chunks(doc, _one_span_per_item(len(items)), token_cap=500)  # ~2000-char chunks
    lens = sorted(len(t) for t in texts)

    assert 2 <= len(texts) < 40  # was 113 chunks on the real doc; several now, not one fragment per header
    assert lens[0] >= _MIN_NONEMPTY_CHARS  # no near-empty / heading-only chunk (was 10)
    assert all(length >= MIN_CHUNK_CHARS for length in lens)  # the size floor holds


# --- 0006-A: structure-first chunking must not fold complete sections away (issue 0006) ----------


_SHORT_CLAUSE_BODIES = [
    "Supplier's total liability shall not exceed the fees paid in the prior twelve months.",
    "This Agreement renews automatically for successive one-year terms unless notice is given.",
    "Each party shall keep the other's Confidential Information secret for five years.",
    "The Seller shall indemnify the Buyer against all third-party claims for defects.",
    "Neither party may assign this Agreement without the other's prior written consent.",
    "This Agreement is governed by the laws of the State of New York.",
]


def _short_clause_doc(n: int = 6) -> SimpleNamespace:
    # a lone TITLE, then n complete SECTIONS (heading + a short one-sentence body), each ~110 chars << the
    # 1000-char prose floor -- exactly RuleWright's 0006 fixture shape.
    items: list[SimpleNamespace] = [_item("Master Services Agreement", "title")]
    for i in range(n):
        items.append(_item(f"{i + 1}. Clause {i + 1}", "section_header", level=1))
        items.append(_item(_SHORT_CLAUSE_BODIES[i % len(_SHORT_CLAUSE_BODIES)]))
    return _doc(items)


def test_structural_sections_survive_the_prose_floor_0006a():
    doc = _short_clause_doc(6)
    spans = StructuralBoundaryDiscoverer().discover(doc)  # (0,0) title + 6 (heading,body) sections

    # the bug: the 1000-char prose floor coalesces every short section into ONE chunk -> structure lost
    prose = _finalize_chunks(doc, spans, token_cap=DEFAULT_TOKEN_CAP)
    assert len(prose) == 1

    # 0006-A: structure-first keeps each docling section as its own chunk, however short
    structural = _finalize_chunks(doc, spans, token_cap=DEFAULT_TOKEN_CAP, respect_structure=True)
    assert len(structural) == 6                                    # one chunk per section, not folded to 1
    assert all("Clause" in t for t in structural)                 # every section survives
    assert "Master Services Agreement" in structural[0]           # the lone TITLE folded forward, not standalone
    assert all("." in t for t in structural)                      # each carries a body sentence, not a lone heading
    assert all(len(t.strip()) >= _MIN_NONEMPTY_CHARS for t in structural)


def test_a_trailing_lone_heading_folds_backward_under_respect_structure_0006a():
    # a document that ends on a bare heading (no body after it) must not leave a heading-only chunk
    doc = _doc([
        _item("1. Term", "section_header", level=1),
        _item("This Agreement runs for one year and then renews automatically each year thereafter."),
        _item("2. Schedule", "section_header", level=1),  # trailing heading with NO body item after it
    ])
    spans = StructuralBoundaryDiscoverer().discover(doc)
    texts = _finalize_chunks(doc, spans, token_cap=DEFAULT_TOKEN_CAP, respect_structure=True)
    assert not any(t.strip() == "2. Schedule" for t in texts)     # the trailing heading never stands alone
    assert "2. Schedule" in texts[-1]                             # it folded backward into the previous section


def test_chunk_end_to_end_preserves_structural_sections_0006a(tmp_path):
    # through the real chunk() with the structural discoverer: a short-clause doc yields one chunk per section
    doc = DoclingDocument(name="short")
    doc.add_text(label="title", text="Master Services Agreement")
    for i, body in enumerate(_SHORT_CLAUSE_BODIES):
        doc.add_text(label="section_header", text=f"{i + 1}. Clause {i + 1}")
        doc.add_text(label="text", text=body)
    parsed = _parsed(tmp_path, doc)

    manifest = chunk(parsed, summarizer=_StubSummarizer(),
                     discoverer=StructuralBoundaryDiscoverer(), cache_dir=tmp_path / "chunks")

    assert len(manifest.chunks) == 6  # NOT folded into 1 by the prose floor (0006-A)


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


def test_asiandragon_real_parse_no_longer_fragments_over_spans():
    files = glob.glob("data/gate2_cache/parsed/ASIANDRAGON*.json")
    if not files:
        pytest.skip("ASIANDRAGON cached parse not present (run the GATE-2 harness to produce it)")
    doc = DoclingDocument.load_from_json(files[0])
    texts = _finalize_chunks(doc, _one_span_per_item(len(doc.texts)), token_cap=500)
    lens = sorted(len(t) for t in texts)

    assert len(texts) < 60  # was 113
    assert lens[0] >= _MIN_NONEMPTY_CHARS  # was 10 near-empty chunks
    print(f"\nASIANDRAGON real spans->finalize: {len(texts)} chunks (was 113); "
          f"char min={lens[0]} median={lens[len(lens) // 2]} max={lens[-1]}")


# --- working-set delivered via the runtime tool, never in the prompt (T36) -----------------------


class _FakeChat(BaseChatModel):
    responder: Any = None

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self.responder(messages))])

    def bind_tools(self, tools, **kwargs):
        return self

    @property
    def _llm_type(self) -> str:
        return "fake-chat"


def test_boundary_discoverer_delivers_the_document_via_the_working_set_tool_not_the_prompt():
    doc = _two_section_doc()  # 4 items

    def responder(messages):
        # after the eval read tools.workingSet(), derive spans from the item count it reported. The spans
        # are correct ONLY if the full 4-item document reached the interpreter via the tool.
        for m in messages:
            if isinstance(m, ToolMessage) and m.name == "eval":
                body = str(m.content)
                info = json.loads(body[body.find("{"): body.rfind("}") + 1]) if "{" in body else {}
                if info.get("n") == 4:
                    return AIMessage(content='[{"start_index": 0, "end_index": 1}, {"start_index": 2, "end_index": 3}]')
                return AIMessage(content="[]")  # working set not delivered -> no valid partition
        # the request carries NO document; the discoverer must have bound it as tools.workingSet()
        code = "const ws = await tools.workingSet(); JSON.stringify({n: ws.length})"
        return AIMessage(content="", tool_calls=[{"name": "eval", "args": {"code": code}, "id": "e1"}])

    # the request the discoverer sends must not contain the document text (it goes through the tool)
    spans = SeamBoundaryDiscoverer(model=_FakeChat(responder=responder)).discover(doc)

    assert [(s.start_index, s.end_index) for s in spans] == [(0, 1), (2, 3)]  # spans derived from tool-delivered ws


def test_working_set_tool_delivers_a_large_document_intact_no_truncation():
    # T36 no-truncation guarantee: a whole document reaches the interpreter via tools.workingSet() with no
    # truncation -- a PTC return marshals as a native JS value and bypasses max_result_chars (which caps
    # only model-facing eval OUTPUT, never a value held in a JS variable). Prove the full item count AND
    # the last item's full text survive.
    doc = DoclingDocument(name="big")
    for i in range(300):
        doc.add_text(label="text", text=f"Clause {i}: " + "lorem ipsum dolor sit amet. " * 10)  # ~290 chars each

    def responder(messages):
        for m in messages:
            if isinstance(m, ToolMessage) and m.name == "eval":
                body = str(m.content)
                info = json.loads(body[body.find("{"): body.rfind("}") + 1]) if "{" in body else {}
                if info.get("n") == 300 and info.get("lastLen", 0) > 200:  # full count + full last text
                    return AIMessage(content='[{"start_index": 0, "end_index": 299}]')
                return AIMessage(content="[]")  # truncated -> invalid
        code = "const ws = await tools.workingSet(); JSON.stringify({n: ws.length, lastLen: ws[ws.length-1].text.length})"
        return AIMessage(content="", tool_calls=[{"name": "eval", "args": {"code": code}, "id": "e1"}])

    spans = SeamBoundaryDiscoverer(model=_FakeChat(responder=responder)).discover(doc)

    assert [(s.start_index, s.end_index) for s in spans] == [(0, 299)]  # whole 300-item doc intact through the tool


# --- registration --------------------------------------------------------------------------------


def test_rlm_chunking_registers_as_an_agent_skill():
    reg = CapabilityRegistry()
    register_rlm_chunking(reg)
    registration = reg.get("rlm_chunking")
    assert registration.kind == "agent_skill"
    assert registration.contract is ChunkManifest
    assert registration.skeleton.response_bounds is None  # agent_skill is loaded, not callable


def test_semantic_chunking_registers_as_a_subgraph():
    from rag_wright.capabilities.rlm_chunking import register_semantic_chunking

    reg = CapabilityRegistry()
    register_semantic_chunking(reg)
    registration = reg.get("semantic_chunking")
    assert registration.kind == "subgraph"  # deterministic single-call chunker (CAP-REG-1b)
    assert registration.contract is ChunkManifest
    assert registration.skeleton.response_bounds is not None  # subgraph is callable


# --- concurrent summarization (async + semaphore backpressure, T19 pattern) ----------------------


def test_summaries_run_concurrently_bounded_by_the_semaphore():
    class _Probe:
        def __init__(self) -> None:
            self._lock = threading.Lock()
            self.inflight = 0
            self.max_inflight = 0

        def summarize(self, text: str) -> str:
            with self._lock:
                self.inflight += 1
                self.max_inflight = max(self.max_inflight, self.inflight)
            time.sleep(0.05)
            with self._lock:
                self.inflight -= 1
            return f"s:{text}"

    probe = _Probe()
    texts = [f"t{i}" for i in range(12)]

    summaries = asyncio.run(_summarize_all(texts, probe, max_concurrency=4))

    assert summaries == [f"s:t{i}" for i in range(12)]  # gather preserves order
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
    assert probe.max_inflight == 1


# --- opt-in live: boundary quality (a real model keeps a coherent unit whole) ---------------------


def _coherent_clause_doc() -> DoclingDocument:
    """A contract with ONE clearly coherent clause spanning items 3..6 (a heading + three sentences that
    plainly belong together). A faithful semantic discoverer keeps 3..6 in one chunk; a fixed-size or
    careless splitter cuts inside it."""
    doc = DoclingDocument(name="coherent")
    doc.add_text(label="section_header", text="1. Definitions")
    doc.add_text(label="text", text="Affiliate means an entity that controls a party to this Agreement. " * 12)
    doc.add_text(label="section_header", text="2. Term")
    # items 3..6 — the coherent indemnification clause
    doc.add_text(label="section_header", text="3. Indemnification")
    doc.add_text(label="text", text="The Seller shall indemnify and hold harmless the Buyer from any loss "
                 "arising out of a breach of the warranties in this Agreement.")
    doc.add_text(label="text", text="Such indemnification shall cover reasonable attorneys' fees and costs "
                 "incurred by the Buyer in connection with any such claim.")
    doc.add_text(label="text", text="This indemnification obligation shall survive the termination of this "
                 "Agreement and remain in effect for a period of three years.")
    doc.add_text(label="section_header", text="4. Governing Law")
    doc.add_text(label="text", text="This Agreement is governed by the laws of the State of Delaware. " * 12)
    return doc


@pytest.mark.model
def test_a_real_model_keeps_a_coherent_clause_in_one_chunk():
    """Live (opt-in, `-m model`): boundary QUALITY, not just presence. The discoverer is handed a document
    with a coherent clause spanning a known item range (3..6); a faithful semantic boundary keeps that
    clause whole, while a fixed-size or every-header splitter cuts inside it. This is the T17 analogue of
    T15's opaque-working-set proof: it tests for GOOD boundaries, the entire value proposition.

    A RED result is first a boundary-quality signal (the discoverer split a coherent unit), not a mechanism
    failure (the mechanism is proven hermetically above)."""
    doc = _coherent_clause_doc()
    spans = SeamBoundaryDiscoverer(token_cap=2000).discover(doc)

    # every item in the coherent clause (3..6) must fall inside a SINGLE span — the clause is not split.
    containing = [s for s in spans if s.start_index <= 3 and s.end_index >= 6]
    assert containing, (
        f"the coherent indemnification clause (items 3..6) was split across chunks; spans="
        f"{[(s.start_index, s.end_index) for s in spans]}"
    )


@pytest.mark.model
def test_real_seam_summarizer_produces_a_summary():
    from rag_wright.capabilities.rlm_chunking import SeamSummarizer

    summary = SeamSummarizer().summarize(
        "This Agreement is entered into by Acme Corporation and governed by Delaware law. "
        "It defines exclusivity, term, and termination."
    )
    assert isinstance(summary, str) and summary.strip()
