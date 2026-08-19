"""RLM chunking capability (FR-I.1): a parsed document -> coherent, capped, summarized chunks.

Chunk boundaries are decided by an **LLM exploring the document** (the RLM machinery, T15): a strong
model reads the parsed document's structure, greps for structural markers, examines section sizes, and
returns semantically coherent boundary spans over the document's items. This is the capability, and it is
mandatory — no fixed-size chunking, ever (SPEC): abrupt fixed-size cuts destroy clause-level retrieval,
and the LLM-found semantic boundary is what prevents that. Recursion is available-when-warranted (a
section too large to judge in one pass may be decomposed further) but is not required of chunking; that
is intrinsic to synthesis (T28) and the RLM method, not chunking (ADR-0019).

Boundary discovery does not need to be reproducible: ingestion chunks a document once and persists the
result; retrieval never re-chunks. Only a document *change* re-chunks (a delete-and-re-chunk, task T34).
Stability of `chunk_id` comes from persistence, not from a repeatable algorithm.

The layer that runs **after** boundaries are chosen is deterministic given those boundaries and lives in
a separate function (`_finalize_chunks`): it joins each span's items, enforces the token cap, applies the
minimum-size floor and tiny-fragment merge (T-CHK — an LLM can just as easily return a boundary around a
lone heading), computes `chunk_id`, and validates. Summaries come from a `Summarizer` seam, filled
concurrently (a flat map, the right shape for independent summaries). Both the boundary discoverer and the
summarizer sit behind seams so the deterministic layer is tested hermetically with stubs and the real
(model-calling) implementations are exercised opt-in.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from rag_wright.capabilities.parsing import ParsedDocument, load_document
from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.corpus.document_parser import _HEADING_LABELS  # the single docling heading-label authority (ADR-0058)
from rag_wright.models.tag_structured import build_tag_structured
from rag_wright.contracts.identifiers import ChunkId
from rag_wright.models.profiles import ModelRole, model_for
from rag_wright.models.seam import build_structured
from rag_wright.skills.rlm.agent import build_rlm_agent, rlm_interpreter_session

DEFAULT_TOKEN_CAP = 20_000
DEFAULT_SUMMARY_CONCURRENCY = 8  # in-flight summary calls (backpressure); summaries are network-bound
_SUMMARIZE_PROMPT = "Summarize this contract chunk in one or two sentences, factually:"

# Minimum chunk size floor (~250 tokens, T-CHK). Applied in `_finalize_chunks` over the DISCOVERER's
# spans: an LLM boundary discoverer can just as easily emit a boundary around a lone heading or a
# two-token fragment as the old mechanical splitter could, so below-floor spans fold into a neighbour and
# heading-only fragments are rejected. The floor coalesces adjacent spans; it never splits.
MIN_CHUNK_CHARS = 1000
_MIN_NONEMPTY_CHARS = 20  # a chunk shorter than this is degenerate (heading-only); never emitted


class BoundaryValidationError(ValueError):
    """Raised when produced chunks violate a boundary invariant (cap, uniqueness, ordering, coverage)."""


class Chunk(BaseModel):
    """One chunk: its stable id, position, text span, summary, and token estimate.

    CU-B1 (ADR-0029): `doc_start`/`doc_end` are this chunk's character range in the CANONICAL document text
    (`canonical_document_text()` = the `_SEP`-join of the finalized chunk texts), so
    `canonical_document_text(chunks)[doc_start:doc_end] == text` byte-faithfully. This is the citation
    coordinate the CUAD highlight pipeline indexes into (span offsets, CU-B2, compose as chunk.doc_start +
    the clause-relative span offset). The chunker strips/merges/splits item text, so the canonical text is
    the reconstruction from chunks (what the app renders + highlights), not the raw parsed text. Page/bbox
    overlay is a separate, DEFERRED concern (CU-B5) but NOT lost: the parsed `DoclingDocument` (persisted at
    parse) retains per-item page+bbox provenance, so a later canonical-char-offset -> Docling-item -> bbox
    mapping stays possible. Optional/defaulted so old cached manifests still validate (a re-chunk regenerates
    the offsets).
    """

    model_config = {"frozen": True}

    chunk_id: str  # canonical ChunkId form: <source_doc_id>:<chunk_index>:<content_hash>
    chunk_index: int
    text: str
    summary: str
    token_estimate: int
    doc_start: int | None = None  # CU-B1: char offset in the canonical document text
    doc_end: int | None = None  # CU-B1: exclusive; canonical_document_text(chunks)[doc_start:doc_end] == text


class ChunkManifest(BaseModel):
    """The per-document chunking output (FR-I.1): the chunk set for one source document."""

    model_config = {"frozen": True}

    source_doc_id: str
    content_hash: str
    token_cap: int
    chunks: list[Chunk] = Field(default_factory=list)


class BoundarySpan(BaseModel):
    """One candidate chunk as an inclusive item-index range over the parsed document's `texts`.

    Spans-over-items is what makes "not fixed-size" structural rather than asserted: a chunk is always a
    contiguous run of the document's own structural items, never an arbitrary character interval. The
    discoverer returns a partition of the document (contiguous, gap-free, covering every item)."""

    model_config = {"frozen": True}

    start_index: int
    end_index: int  # inclusive


@runtime_checkable
class BoundaryDiscoverer(Protocol):
    """The semantic boundary-discovery seam: decide chunk boundaries as a partition of item-index spans."""

    def discover(self, document) -> list[BoundarySpan]: ...


# --- the real, LLM-driven boundary discoverer (opt-in / live) ------------------------------------

_DISCOVERY_INSTRUCTIONS = (
    "Call `const items = await tools.workingSet();` to get the parsed document as a list of structural "
    "items (each has id, index, label, level, and full text); it is a JavaScript value in the interpreter, "
    "never in your context. LOAD CHECK (do not skip): immediately verify you loaded the WHOLE document — "
    "`const _n = await tools.workingSetSize(); if (items.length !== _n) throw new Error('LOAD UNDER-READ: ' "
    "+ items.length + ' of ' + _n);` — the size is the truthful runtime count and a short load is a silent "
    "text drop. Partition it into semantically coherent chunks by grouping CONTIGUOUS items so "
    "that each chunk is one coherent unit (a clause, a section, a related run) and no chunk exceeds ~{cap} "
    "characters. Never split a single coherent clause across two chunks, and never cut at a fixed size. "
    "COVERAGE TAIL (do not skip): before returning, verify in code that your spans cover EVERY item from 0 "
    "to items.length-1 with no gap; for any item range the recursion missed, add a span covering it — the "
    "code guarantees coverage, a missed item is a silent text drop. Return ONLY a JSON array of the "
    "boundaries as objects {{\"start_index\": i, \"end_index\": j}} (inclusive, contiguous, covering every "
    "item from 0 to the last)."
)


def _document_items(document) -> list[dict]:
    """The document's items as data for the working-set tool: id, index, label, level, and FULL text. This
    is a JS value delivered through `tools.workingSet()` — it lives in the interpreter and never enters the
    model's context (no truncation; a whole document survives intact), so no `peek` tool is needed. `id`
    lets the workflow's coverage tail diff handled items against the whole set (T37)."""
    items: list[dict] = []
    for i, item in enumerate(document.texts):
        items.append(
            {
                "id": i,
                "index": i,
                "label": str(getattr(item, "label", "")),
                "level": getattr(item, "level", None),
                "text": (getattr(item, "text", "") or "").strip(),
            }
        )
    return items


def _extract_spans(text: str) -> list[BoundarySpan]:
    """Parse the last JSON array of {start_index, end_index} objects from the model's final output."""
    end = text.rfind("]")
    start = text.rfind("[", 0, end)
    if start == -1 or end == -1:
        raise BoundaryValidationError("boundary discoverer returned no JSON span array")
    raw = json.loads(text[start : end + 1])
    return [BoundarySpan(start_index=int(o["start_index"]), end_index=int(o["end_index"])) for o in raw]


class SeamBoundaryDiscoverer:
    """The real discoverer: a strong model explores the document via the RLM machinery and returns spans.

    Uses the STRUCTURED_REASONING role (deepseek-v4-pro, the quality-sensitive model) through the T15
    `build_rlm_agent` machinery: the document's structural view is handed to the orchestrator, which may
    `peek()` at item text and (for a section too large to judge in one pass) recurse via the sub-agents —
    available-when-warranted, not forced. It returns a partition of boundary spans.
    """

    def __init__(self, model: object = None, *, token_cap: int = DEFAULT_TOKEN_CAP) -> None:
        # `model` is a model id (str) resolved through the profile seam, or a `BaseChatModel` instance
        # (tests inject a fake); defaults to the quality-sensitive STRUCTURED_REASONING role.
        self._model = model if model is not None else model_for(ModelRole.STRUCTURED_REASONING)
        self._token_cap = token_cap

    def discover(self, document) -> list[BoundarySpan]:
        items = _document_items(document)

        @tool
        def working_set() -> list:
            """Return the working set: the parsed document's items (id, index, label, level, full text)."""
            return items

        @tool
        def working_set_size() -> int:
            """Return the number of items in the delivered working set (a truthful count the workflow's
            load-completeness assertion checks, so an under-read of the document fails loud, not silent)."""
            return len(items)

        instructions = _DISCOVERY_INSTRUCTIONS.format(cap=self._token_cap * 4)
        request = f"Run this as a workflow.\n\n{instructions}"
        # one model across all roles (the strong reasoning model): boundary discovery is a single
        # exploration, and a sub-agent it dispatches for an over-large section warrants the same model.
        # The working set is delivered as a PTC value (`tools.workingSet()`, T36): it stays in the
        # interpreter and never enters the model's context. The interpreter session is serialized
        # process-wide (KI-1, ADR-0020): build + run + teardown all inside the lock.
        with rlm_interpreter_session(ptc=[working_set, working_set_size]) as interpreter:
            agent = build_rlm_agent(
                reasoning_model=self._model, decomposer_model=self._model, worker_model=self._model,
                interpreter=interpreter,
            )
            messages = agent.invoke({"messages": [HumanMessage(content=request)]})["messages"]
        final = _final_text(messages)
        spans = _extract_spans(final)
        # the workflow's coverage tail (in the skill, T37) guarantees full coverage; validate loudly here
        # so an incomplete run fails visibly rather than silently dropping document text.
        _validate_partition(spans, len(document.texts))
        return spans

    async def adiscover(self, document) -> list[BoundarySpan]:
        # ASYNC-B2a: the agentic (RLM) discoverer is the optional path (production uses SingleCall); run its sync
        # agent off the event loop. The deadline-critical production discoverer (SingleCall) is truly async.
        return await asyncio.to_thread(self.discover, document)


def _final_text(messages) -> str:
    """The model's final answer text (the last non-empty assistant message, or the last eval result)."""
    for message in reversed(messages):
        if isinstance(message, AIMessage) and (message.text or "").strip():
            return message.text
    for message in reversed(messages):
        if isinstance(message, ToolMessage) and message.name == "eval":
            return str(message.content)
    return ""


# --- single-call (non-agentic) boundary discovery + deterministic repair (CU-B4) ------------------

_SINGLE_CALL_PROMPT = (
    "Below are {n} numbered structural items of a contract. Partition them into semantically coherent "
    "clauses/sections: CONTIGUOUS groups of item indices, each one coherent clause or section (never split a "
    "single clause), together covering EVERY item from 0 to {last} with no gap or overlap. Return the spans "
    "as {{start_index, end_index}} (inclusive).\n\n{body}"
)


class _BoundaryList(BaseModel):
    spans: list[BoundarySpan]


def repair_partition(raw: list[tuple[int, int]], n: int) -> list[BoundarySpan]:
    """Deterministically repair ANY set of (start, end) index ranges into a VALID partition of [0, n): each
    span START in (0, n) becomes a break-before point, and the partition is the contiguous, gap-free run of
    item ranges between the (sorted, de-duplicated) breaks -- always covering every item 0..n-1, ordered,
    non-empty. Robust to overlap / gap / out-of-range / start>end / unordered / duplicate input, because a
    single LLM call (unlike the agentic coverage-tail) does not guarantee a clean partition. No valid break ->
    one span over the whole document (valid, if coarse)."""
    if n <= 0:
        return []
    breaks = sorted({int(s) for (s, _e) in raw if 0 < int(s) < n})
    bounds = [0, *breaks, n]
    return [BoundarySpan(start_index=bounds[i], end_index=bounds[i + 1] - 1) for i in range(len(bounds) - 1)]


class SingleCallBoundaryDiscoverer:
    """A NON-agentic boundary discoverer: ONE structured LLM call -> a boundary partition, deterministically
    repaired (`repair_partition`) to a valid partition. For well-structured documents (CUAD contracts) this
    replaces the agentic `SeamBoundaryDiscoverer` at ~100x less cost/latency (measured 3.4s vs 5-9min) with
    equal clause integrity. Uses the GENERAL role (Gemma-4-class). No interpreter -> no process-wide lock ->
    ordinary async concurrency (no process pool needed). `structured_factory` is injectable for tests."""

    def __init__(self, model_id: str | None = None, *, structured_factory=build_structured) -> None:
        self._model_id = model_id or model_for(ModelRole.GENERAL)
        self._factory = structured_factory

    def _prompt(self, document) -> tuple[str | None, int]:
        items = _document_items(document)
        n = len(items)
        if n == 0:
            return None, 0
        body = "\n".join(f"[{it['index']}] {it['text'][:140]}" for it in items)
        return _SINGLE_CALL_PROMPT.format(n=n, last=n - 1, body=body), n

    # ADR-0058 side-fix (issue 0004): name the stage so a deadline warning says WHICH call was cancelled.
    _STAGE = "semantic_chunking.discover"

    def discover(self, document) -> list[BoundarySpan]:
        prompt, n = self._prompt(document)
        if prompt is None:
            return []
        out = self._factory(self._model_id, _BoundaryList, label=self._STAGE).invoke(prompt)
        return repair_partition([(s.start_index, s.end_index) for s in out.spans], n)

    async def adiscover(self, document) -> list[BoundarySpan]:
        # ASYNC-B2a (ADR-0057): the structured boundary call on the async seam (true wall-clock deadline).
        prompt, n = self._prompt(document)
        if prompt is None:
            return []
        out = await self._factory(self._model_id, _BoundaryList, label=self._STAGE).ainvoke(prompt)
        return repair_partition([(s.start_index, s.end_index) for s in out.spans], n)


_CUT_PROMPT = (
    "Below are {n} numbered structural items of a document (indices 0 to {last}). Group CONTIGUOUS items into "
    "semantically coherent clauses/sections (never split a single clause). List the item index where EACH new "
    "chunk BEGINS -- the first item of every clause/section, in increasing order; index 0 always begins the "
    "first chunk.\n\n{body}"
)


class _CutIndices(BaseModel):
    """CHUNK-3 (ADR-0058): the boundary output as a FLAT list of chunk-START item indices (a `list[int]`), so it
    fits `tag_structured`'s flat-schema scope -- no nested `list[BaseModel]`. `repair_partition` forces full,
    valid coverage regardless of what the model returns (missing 0, out-of-range, dupes, unordered)."""

    cuts: list[int] = Field(default_factory=list,
                            description="the item index where each new chunk begins, one per line")


def _cuts_to_spans(cuts: list[int], n: int) -> list[BoundarySpan]:
    """A flat cut-index list (chunk-start indices) -> a valid `BoundarySpan` partition. Each cut is a
    break-before point; `repair_partition` sorts/de-dupes/drops out-of-range and guarantees a contiguous,
    gap-free partition covering 0..n-1 (so a bad model list can never lose text)."""
    return repair_partition([(c, c) for c in cuts], n)


class TagBoundaryDiscoverer:
    """CHUNK-3 (ADR-0058): boundary discovery via CLIENT-SIDE tag-parse (`build_tag_structured`, ADR-0045) over
    the FLAT `_CutIndices` contract -- the model emits chunk-start indices in light XML tags, parsed on our side.
    Unlike the server-side guided-decoding `_BoundaryList` call (which ran away past the 180s deadline on
    self-hosted Granite), a free-text tag call terminates cleanly. Seam-compatible; CHUNK-4's per-section
    fallback runs it over one section's items at a time (small, bounded input). `structured_factory` injectable."""

    _STAGE = "semantic_chunking.discover"

    def __init__(self, model_id: str | None = None, *, structured_factory=build_tag_structured) -> None:
        self._model_id = model_id or model_for(ModelRole.GENERAL)
        self._factory = structured_factory

    def _prompt(self, document) -> tuple[str | None, int]:
        items = _document_items(document)
        n = len(items)
        if n == 0:
            return None, 0
        body = "\n".join(f"[{it['index']}] {it['text'][:140]}" for it in items)
        return _CUT_PROMPT.format(n=n, last=n - 1, body=body), n

    def discover(self, document) -> list[BoundarySpan]:
        prompt, n = self._prompt(document)
        if prompt is None:
            return []
        out = self._factory(self._model_id, _CutIndices, label=self._STAGE).invoke(prompt)
        return _cuts_to_spans(out.cuts, n)

    async def adiscover(self, document) -> list[BoundarySpan]:
        prompt, n = self._prompt(document)
        if prompt is None:
            return []
        out = await self._factory(self._model_id, _CutIndices, label=self._STAGE).ainvoke(prompt)
        return _cuts_to_spans(out.cuts, n)


class StructuralBoundaryDiscoverer:
    """Tier-1 (ADR-0058, issue 0004): DETERMINISTIC boundaries from docling's structural labels -- a new chunk
    starts at every heading item (SECTION_HEADER / TITLE / FIELD_HEADING, the `_HEADING_LABELS` authority docling
    already assigns while parsing). NO model call, so cost is O(items) and INDEPENDENT of document length -- the
    fix for the whole-document boundary call that blew the 180s deadline. Generic across any docling-parsed
    document (contract, policy, regulation), because the structural labels are domain-neutral.

    Only decides the semantic boundaries; the token cap (hard-split) and the lone-heading fold are enforced
    downstream by `_finalize_chunks`, and `repair_partition` guarantees a valid partition. A document with NO
    headings degrades to one span (then cap-split) -- the case CHUNK-4's per-section model fallback improves."""

    def _starts(self, document) -> list[int]:
        # boundary START indices: 0, plus every heading-labelled item's index (deduped, sorted).
        return sorted({0} | {i for i, item in enumerate(document.texts)
                             if getattr(item, "label", None) in _HEADING_LABELS})

    def discover(self, document) -> list[BoundarySpan]:
        n = len(document.texts)
        if n == 0:
            return []
        starts = self._starts(document)
        pairs = [(starts[k], (starts[k + 1] - 1 if k + 1 < len(starts) else n - 1)) for k in range(len(starts))]
        return repair_partition(pairs, n)  # safety net; the partition is valid by construction

    async def adiscover(self, document) -> list[BoundarySpan]:
        return self.discover(document)  # deterministic, no IO/model -> the async twin just delegates


class _SubDocument:
    """A contiguous item-range viewed as a stand-alone document (`.texts`), so a per-section refine call runs the
    model over ONLY that section's items (0-based indices) -- never the whole document."""

    __slots__ = ("texts",)

    def __init__(self, texts: list) -> None:
        self.texts = texts


class StructuralModelFallbackDiscoverer:
    """CHUNK-4 (ADR-0058, issue 0004, Tier-2 b1): structural boundaries first (deterministic, no model); any
    section that would be HARD-SPLIT by the token cap (over-cap -- and structureless by construction, since the
    structural pass already cut at every heading) is refined by a BOUNDED PER-SECTION model call
    (`TagBoundaryDiscoverer`, tag-parse), run CONCURRENTLY. The model only ever sees ONE over-cap section at a
    time, so cost never scales with document length. A section within the cap keeps its structural boundary (no
    model call); a fully-structured document makes ZERO model calls. `structural`/`fallback` are injectable."""

    def __init__(self, model_id: str | None = None, *, token_cap: int = DEFAULT_TOKEN_CAP,
                 structural: Any = None, fallback: Any = None,
                 max_concurrency: int = DEFAULT_SUMMARY_CONCURRENCY) -> None:
        self._cap_chars = token_cap * 4  # matches _finalize_chunks' hard-split trigger exactly
        self._structural = structural if structural is not None else StructuralBoundaryDiscoverer()
        self._fallback = fallback if fallback is not None else TagBoundaryDiscoverer(model_id)
        self._max_concurrency = max_concurrency

    def _needs_refine(self, document, span: BoundarySpan) -> bool:
        # exactly the multi-item sections _finalize_chunks would hard-split (fixed-size, SPEC-forbidden). A
        # single over-cap item cannot be split by boundaries -> left for finalize's hard-split.
        return span.end_index > span.start_index and len(_join_span(document, span)) > self._cap_chars

    def _map_back(self, sub_spans: list[BoundarySpan], span: BoundarySpan) -> list[BoundarySpan]:
        offset = span.start_index
        mapped = [BoundarySpan(start_index=s.start_index + offset, end_index=s.end_index + offset)
                  for s in sub_spans]
        return mapped or [span]  # a non-splittable section keeps its span (finalize hard-splits it)

    def discover(self, document) -> list[BoundarySpan]:
        out: list[BoundarySpan] = []
        for span in self._structural.discover(document):
            if self._needs_refine(document, span):
                sub = _SubDocument(document.texts[span.start_index:span.end_index + 1])
                out.extend(self._map_back(self._fallback.discover(sub), span))
            else:
                out.append(span)
        return out

    async def adiscover(self, document) -> list[BoundarySpan]:
        base = await self._structural.adiscover(document)
        sem = asyncio.Semaphore(self._max_concurrency)

        async def _one(span: BoundarySpan) -> list[BoundarySpan]:
            if not self._needs_refine(document, span):
                return [span]
            sub = _SubDocument(document.texts[span.start_index:span.end_index + 1])
            async with sem:  # bound the concurrent per-section model calls
                sub_spans = await self._fallback.adiscover(sub)
            return self._map_back(sub_spans, span)

        groups = await asyncio.gather(*(_one(span) for span in base))  # order preserved
        return [sp for group in groups for sp in group]


@runtime_checkable
class Summarizer(Protocol):
    """The per-chunk summarization seam."""

    def summarize(self, text: str) -> str: ...


class _Summary(BaseModel):
    summary: str


class SeamSummarizer:
    """The real summarizer: a structured-output call through the model-profile seam.

    Uses the SUMMARIZATION role (a smaller model, FR-I.6). Structured output plus the seam's
    temperature-zero base keep summaries stable per call.
    """

    def __init__(self, model_id: str | None = None) -> None:
        self._model_id = model_id or model_for(ModelRole.SUMMARIZATION)

    def summarize(self, text: str) -> str:
        result = build_structured(self._model_id, _Summary).invoke(f"{_SUMMARIZE_PROMPT}\n\n{text}")
        return result.summary


def _estimate_tokens(text: str) -> int:
    """A tokenizer-free token estimate (~4 chars/token)."""
    return max(1, len(text) // 4)


def _hard_split(text: str, token_cap: int) -> list[str]:
    """Split text that alone exceeds the cap into <= cap-sized pieces (by chars)."""
    cap_chars = token_cap * 4
    return [text[i : i + cap_chars] for i in range(0, len(text), cap_chars)]


_SEP = "\n\n"


def _chunk_offsets(texts: list[str]) -> list[tuple[int, int]]:
    """CU-B1: the (start, end) char range of each finalized chunk text in the canonical document text
    (the `_SEP`-join of the chunk texts). Deterministic cumulative positions; by construction
    `_SEP.join(texts)[start:end] == texts[i]`."""
    offsets: list[tuple[int, int]] = []
    pos = 0
    for text in texts:
        offsets.append((pos, pos + len(text)))
        pos += len(text) + len(_SEP)  # the trailing +_SEP on the last is unused (no trailing separator)
    return offsets


def canonical_document_text(chunks: list[Chunk]) -> str:
    """CU-B1: the document text the highlight offsets index into -- the `_SEP`-join of the finalized chunk
    texts. The app renders and highlights on THIS (whitespace-normalized) reconstruction; each chunk/span's
    `doc_start`/`doc_end` slice it byte-faithfully. Not the raw parsed text (the chunker strips/merges)."""
    return _SEP.join(c.text for c in chunks)


def _validate_partition(spans: list[BoundarySpan], n_items: int) -> None:
    """The discoverer must return a contiguous, gap-free partition covering every item (no lost text)."""
    if n_items == 0:
        return
    if not spans:
        raise BoundaryValidationError("boundary discoverer returned no spans")
    ordered = sorted(spans, key=lambda s: s.start_index)
    if ordered[0].start_index != 0 or ordered[-1].end_index != n_items - 1:
        raise BoundaryValidationError("boundary spans do not cover the whole document")
    for prev, nxt in zip(ordered, ordered[1:]):
        if nxt.start_index != prev.end_index + 1:
            raise BoundaryValidationError("boundary spans overlap or leave a gap")
        if prev.end_index < prev.start_index:
            raise BoundaryValidationError("boundary span end precedes its start")


def _join_span(document, span: BoundarySpan) -> str:
    """Join the non-empty text of the items a span covers (the deterministic-given-boundaries join)."""
    parts = []
    for i in range(span.start_index, span.end_index + 1):
        text = (getattr(document.texts[i], "text", "") or "").strip()
        if text:
            parts.append(text)
    return _SEP.join(parts)


def _finalize_chunks(document, spans: list[BoundarySpan], token_cap: int) -> list[str]:
    """Deterministic given the boundaries: join each span, enforce the cap, apply the T-CHK floor/merge.

    Runs after the discoverer chooses boundaries and is independent of how they were found. The token cap
    always wins (an over-cap span is hard-split); below-floor spans fold into a neighbour so a discoverer
    that returned a lone-heading or two-token span does not become a tiny fragment (T-CHK)."""
    cap_chars = token_cap * 4
    floor = min(MIN_CHUNK_CHARS, cap_chars)  # a tiny cap (tests) cannot demand a larger floor
    texts: list[str] = []
    for span in spans:
        text = _join_span(document, span)
        if not text:
            continue
        if len(text) > cap_chars:  # cap safety net: a single over-cap span is hard-split
            texts.extend(_hard_split(text, token_cap))
        else:
            texts.append(text)
    return _merge_below_floor(texts, floor, cap_chars)


def _merge_below_floor(chunks: list[str], floor: int, cap_chars: int) -> list[str]:
    """Fold any below-floor chunk into an adjacent chunk (backward first, then the first chunk forward),
    never exceeding the cap. Eliminates heading-only/near-empty fragments; a lone chunk is left as-is."""
    if len(chunks) <= 1:
        return chunks
    merged: list[str] = []
    for c in chunks:
        if merged and len(c) < floor and len(merged[-1]) + len(_SEP) + len(c) <= cap_chars:
            merged[-1] = merged[-1] + _SEP + c  # fold backward into the previous chunk
        else:
            merged.append(c)
    if len(merged) > 1 and len(merged[0]) < floor and len(merged[0]) + len(_SEP) + len(merged[1]) <= cap_chars:
        merged[1] = merged[0] + _SEP + merged[1]  # a below-floor first chunk folds forward
        merged.pop(0)
    # a below-floor LAST chunk that could not fold backward within the cap (a small residual after a full
    # chunk): fold it back if it fits, else rebalance the pair into two >= floor halves, so the floor
    # holds even when a discoverer emits a tiny trailing span (T-CHK). Only when the floor is genuinely
    # below the cap (real usage: floor 1000 << cap); a degenerate floor==cap leaves a cap-forced residual.
    if len(merged) > 1 and len(merged[-1]) < floor and floor < cap_chars:
        tail = merged.pop()
        combined = merged[-1] + _SEP + tail
        if len(combined) <= cap_chars:
            merged[-1] = combined
        else:
            half = len(combined) // 2  # both halves land in [floor, cap] since floor < cap and combined > cap
            merged[-1] = combined[:half]
            merged.append(combined[half:])
    return merged


def _validate_boundaries(chunks: list[Chunk], token_cap: int) -> None:
    if not chunks:
        raise BoundaryValidationError("no chunks produced")
    ids = [c.chunk_id for c in chunks]
    if len(ids) != len(set(ids)):
        raise BoundaryValidationError("duplicate chunk_ids")
    for index, chunk_ in enumerate(chunks):
        if chunk_.chunk_index != index:
            raise BoundaryValidationError(f"non-sequential chunk_index at {index}")
        if not chunk_.text.strip():
            raise BoundaryValidationError(f"empty chunk at {index}")
        if len(chunks) > 1 and len(chunk_.text.strip()) < _MIN_NONEMPTY_CHARS:
            raise BoundaryValidationError(f"near-empty (heading-only) chunk at {index} (T-CHK)")
        if chunk_.token_estimate > token_cap:
            raise BoundaryValidationError(f"chunk {index} exceeds token cap ({token_cap})")


async def _summarize_all(
    texts: list[str], summarizer: Summarizer, max_concurrency: int
) -> list[str]:
    """Summarize chunk texts concurrently, bounded by a semaphore (the embedding capability's
    async+backpressure pattern, T19). Each blocking `summarize()` runs in a thread so the network-bound
    calls overlap; the semaphore caps in-flight requests. `gather` preserves order, so each summary lines
    up with its text. Summarization is a flat map (independent per chunk), which is the right shape."""
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _one(text: str) -> str:
        async with semaphore:  # backpressure
            return await asyncio.to_thread(summarizer.summarize, text)

    return list(await asyncio.gather(*(_one(text) for text in texts)))


def chunk(
    parsed: ParsedDocument,
    *,
    summarizer: Summarizer,
    cache_dir: Path,
    discoverer: Optional[BoundaryDiscoverer] = None,
    token_cap: int = DEFAULT_TOKEN_CAP,
    max_concurrency: int = DEFAULT_SUMMARY_CONCURRENCY,
) -> ChunkManifest:
    """Chunk a parsed document into a `ChunkManifest`, content-hash gated (chunked once, then persisted).

    An LLM `discoverer` chooses the semantic boundaries (defaults to the live `SeamBoundaryDiscoverer`;
    hermetic tests inject a stub); `_finalize_chunks` turns its spans into capped, floor-respecting chunk
    texts; each is summarized concurrently; boundaries are validated; the manifest is cached. If a manifest
    for this document's content hash already exists it is reused (the gate; no re-chunk, no LLM call).
    """
    cached, manifest_path, document, disc = _chunk_prepare(parsed, cache_dir, discoverer, token_cap)
    if cached is not None:
        return cached
    spans = disc.discover(document)
    texts = _chunk_texts(document, spans, token_cap)
    summaries = asyncio.run(_summarize_all(texts, summarizer, max_concurrency))
    return _chunk_manifest(parsed, texts, summaries, token_cap, manifest_path)


async def achunk(
    parsed: ParsedDocument,
    *,
    summarizer: Summarizer,
    cache_dir: Path,
    discoverer: Optional[BoundaryDiscoverer] = None,
    token_cap: int = DEFAULT_TOKEN_CAP,
    max_concurrency: int = DEFAULT_SUMMARY_CONCURRENCY,
) -> ChunkManifest:
    """ASYNC-B2a (ADR-0057): the async twin of `chunk`. Awaits the discoverer's async boundary call (the true
    wall-clock deadline) and the concurrent summarize directly (no `asyncio.run` island), so it runs on the
    ingestion event loop. Same content-hash gate + manifest as `chunk`."""
    cached, manifest_path, document, disc = _chunk_prepare(parsed, cache_dir, discoverer, token_cap)
    if cached is not None:
        return cached
    spans = await disc.adiscover(document)
    texts = _chunk_texts(document, spans, token_cap)
    summaries = await _summarize_all(texts, summarizer, max_concurrency)
    return _chunk_manifest(parsed, texts, summaries, token_cap, manifest_path)


def _chunk_prepare(parsed: ParsedDocument, cache_dir: Path, discoverer: Optional[BoundaryDiscoverer],
                   token_cap: int) -> tuple[Optional[ChunkManifest], Path, Any, Any]:
    """Shared chunk/achunk head: the content-hash gate (return the cached manifest if present) and, otherwise,
    the loaded document + resolved discoverer."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = cache_dir / f"{parsed.source_doc_id}.{parsed.content_hash[:16]}.chunks.json"
    if manifest_path.exists():  # content-hash gate
        return ChunkManifest.model_validate_json(manifest_path.read_text(encoding="utf-8")), manifest_path, None, None
    document = load_document(parsed)
    disc = discoverer if discoverer is not None else SeamBoundaryDiscoverer(token_cap=token_cap)
    return None, manifest_path, document, disc


def _chunk_texts(document: Any, spans: list[BoundarySpan], token_cap: int) -> list[str]:
    """Shared: validate the boundary partition and finalize it into capped chunk texts."""
    _validate_partition(spans, len(document.texts))
    return _finalize_chunks(document, spans, token_cap)


def _chunk_manifest(parsed: ParsedDocument, texts: list[str], summaries: list[str], token_cap: int,
                    manifest_path: Path) -> ChunkManifest:
    """Shared chunk/achunk tail: assemble + validate the chunks, write and return the manifest."""
    offsets = _chunk_offsets(texts)  # CU-B1: char ranges in the canonical document text
    chunks = [
        Chunk(
            chunk_id=ChunkId.of(parsed.source_doc_id, index, text).value,
            chunk_index=index,
            text=text,
            summary=summary,
            token_estimate=_estimate_tokens(text),
            doc_start=offsets[index][0],
            doc_end=offsets[index][1],
        )
        for index, (text, summary) in enumerate(zip(texts, summaries))
    ]
    _validate_boundaries(chunks, token_cap)
    manifest = ChunkManifest(
        source_doc_id=parsed.source_doc_id, content_hash=parsed.content_hash,
        token_cap=token_cap, chunks=chunks)
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return manifest


def register_rlm_chunking(registry: CapabilityRegistry) -> None:
    """Register the RLM chunking capability (FR-I.1) as an `agent_skill` (it applies `rlm_method`)."""
    registry.register(
        "rlm_chunking",
        contract=ChunkManifest,
        kind="agent_skill",
        display_name="RLM chunking",
    )


def register_semantic_chunking(registry: CapabilityRegistry) -> None:
    """Register the single-call semantic chunking capability (FR-I.1) as a `subgraph` — the deterministic
    (non-RLM) chunker: single-call boundary discovery + deterministic repair + content-hash gate (CU-B4).
    Distinct from `rlm_chunking` (the dynamic RLM discoverer, an agent_skill); CAP-REG-1b."""
    registry.register(
        "semantic_chunking",
        contract=ChunkManifest,
        kind="subgraph",
        display_name="Semantic chunking (single-call)",
    )
