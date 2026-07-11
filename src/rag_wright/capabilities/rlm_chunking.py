"""RLM chunking capability (FR-I.1): a parsed document -> coherent, capped, summarized chunks.

Applies the RLM method (FR-C.10, `rlm_method`): the whole parsed document is loaded into the
interpreter (not bounded by a context window), sliced along topic/section/chapter boundaries in
code, and each slice is dispatched to a summarizer. Boundaries and `chunk_id`s are computed
deterministically from the document's section structure plus a ~20,000-token cap, so identical input
yields identical boundaries and ids across runs; summaries come from the model-profile seam under
structured output / temperature zero, also deterministic. Chunking is content-hash gated: an
unchanged document is not re-chunked.

The summarizer sits behind a `Summarizer` seam so the deterministic slice/gate logic is tested
hermetically with a stub, and the real (model-calling) summarizer is exercised opt-in. Grounded on
`ChunkId` (T1), the parsing capability (T16), and the model-profile seam (T11).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from rag_wright.capabilities.parsing import ParsedDocument, load_document
from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.contracts.identifiers import ChunkId
from rag_wright.models.profiles import ModelRole, model_for
from rag_wright.models.seam import build_structured

DEFAULT_TOKEN_CAP = 20_000
DEFAULT_SUMMARY_CONCURRENCY = 8  # in-flight summary calls (backpressure); summaries are network-bound
_HEADER_LABELS = ("section_header", "title")  # a heading is a candidate section boundary
_SUMMARIZE_PROMPT = "Summarize this contract chunk in one or two sentences, factually:"

# Minimum chunk size floor (~250 tokens, T-CHK). A header only STARTS a new chunk once the current
# chunk meets this floor; below-floor sections fold into their neighbour, so a document whose parser
# over-detected headings (e.g. an OCR'd contract where every numbered clause is a level-1 heading) is
# not shattered into tiny fragments. The floor coalesces boundaries within a parent section; it never
# splits a subsection away from its parent (deeper headings keep accumulating). Tunable.
MIN_CHUNK_CHARS = 1000
_MIN_NONEMPTY_CHARS = 20  # a chunk shorter than this is degenerate (heading-only); never emitted


class BoundaryValidationError(ValueError):
    """Raised when produced chunks violate a boundary invariant (cap, uniqueness, ordering)."""


class Chunk(BaseModel):
    """One chunk: its stable id, position, text span, summary, and token estimate."""

    model_config = {"frozen": True}

    chunk_id: str  # canonical ChunkId form: <source_doc_id>:<chunk_index>:<content_hash>
    chunk_index: int
    text: str
    summary: str
    token_estimate: int


class ChunkManifest(BaseModel):
    """The per-document chunking output (FR-I.1): the chunk set for one source document."""

    model_config = {"frozen": True}

    source_doc_id: str
    content_hash: str
    token_cap: int
    chunks: list[Chunk] = Field(default_factory=list)


@runtime_checkable
class Summarizer(Protocol):
    """The per-chunk summarization seam."""

    def summarize(self, text: str) -> str: ...


class _Summary(BaseModel):
    summary: str


class SeamSummarizer:
    """The real summarizer: a structured-output call through the model-profile seam (deterministic).

    Uses the SUMMARIZATION role (a smaller model, FR-I.6). Structured output plus the seam's
    temperature-zero base make the summary deterministic.
    """

    def __init__(self, model_id: str | None = None) -> None:
        self._model_id = model_id or model_for(ModelRole.SUMMARIZATION)

    def summarize(self, text: str) -> str:
        result = build_structured(self._model_id, _Summary).invoke(f"{_SUMMARIZE_PROMPT}\n\n{text}")
        return result.summary


def _estimate_tokens(text: str) -> int:
    """A deterministic, tokenizer-free token estimate (~4 chars/token)."""
    return max(1, len(text) // 4)


def _hard_split(text: str, token_cap: int) -> list[str]:
    """Split text that alone exceeds the cap into <= cap-sized pieces (deterministic, by chars)."""
    cap_chars = token_cap * 4
    return [text[i : i + cap_chars] for i in range(0, len(text), cap_chars)]


_SEP = "\n\n"


def _split_into_chunks(document, token_cap: int) -> list[str]:
    """Slice the parsed document into coherent, capped chunk texts (deterministic, code-side).

    A new chunk starts at a section heading only once the current chunk has reached the size floor and
    the heading is a *major* boundary (same or higher level than the section the chunk opened with);
    a deeper subsection heading, or a heading reached before the floor, keeps accumulating, so tiny
    sections fold into their neighbour within the same parent rather than becoming their own fragment
    (T-CHK). The cap always wins: a chunk is flushed when the token cap would be exceeded, and a single
    over-cap item is hard-split. The cap is checked against the actual *joined* length so no chunk
    slips over through per-item rounding. Any residual below-floor chunk is merged post hoc.
    """
    cap_chars = token_cap * 4  # _estimate_tokens is len // 4, so the cap in characters
    floor = min(MIN_CHUNK_CHARS, cap_chars)  # a tiny cap (tests) cannot demand a larger floor
    chunks: list[str] = []
    buffer: list[str] = []
    buffer_len = 0  # len(_SEP.join(buffer))
    section_level: int | None = None  # level of the heading that opened the current chunk's section

    def flush() -> None:
        nonlocal buffer, buffer_len, section_level
        if buffer:
            chunks.append(_SEP.join(buffer))
            buffer, buffer_len, section_level = [], 0, None

    for item in document.texts:
        text = (getattr(item, "text", "") or "").strip()
        if not text:
            continue
        label = str(getattr(item, "label", "")).lower()
        is_header = any(h in label for h in _HEADER_LABELS)
        level = getattr(item, "level", None) if is_header else None

        if is_header and buffer and buffer_len >= floor:
            major = section_level is None or level is None or level <= section_level
            if major:  # enough content AND a same-or-higher-level boundary -> a real section break
                flush()

        if len(text) > cap_chars:  # a single item larger than the cap
            flush()
            chunks.extend(_hard_split(text, token_cap))
            continue

        added = (len(_SEP) if buffer else 0) + len(text)
        if buffer and buffer_len + added > cap_chars:  # cap boundary (measured on the joined text)
            flush()
            added = len(text)
        if not buffer and is_header:  # this heading opens a new chunk's section
            section_level = level
        buffer.append(text)
        buffer_len += added

    flush()
    return _merge_below_floor(chunks, floor, cap_chars)


def _merge_below_floor(chunks: list[str], floor: int, cap_chars: int) -> list[str]:
    """Fold any residual below-floor chunk into an adjacent chunk (backward first, then the first
    chunk forward), never exceeding the cap. Eliminates heading-only/near-empty fragments left by a
    cap-forced flush or a tiny trailing section; a lone chunk is left as-is (a short document)."""
    if len(chunks) <= 1:
        return chunks
    merged: list[str] = []
    for c in chunks:
        if merged and len(c) < floor and len(merged[-1]) + len(_SEP) + len(c) <= cap_chars:
            merged[-1] = merged[-1] + _SEP + c  # fold backward into the previous chunk (same neighbour)
        else:
            merged.append(c)
    if len(merged) > 1 and len(merged[0]) < floor and len(merged[0]) + len(_SEP) + len(merged[1]) <= cap_chars:
        merged[1] = merged[0] + _SEP + merged[1]  # a below-floor first chunk folds forward
        merged.pop(0)
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
    async+backpressure pattern, T19). Each blocking `summarize()` runs in a thread (`asyncio.to_thread`)
    so the network-bound calls overlap; the semaphore caps in-flight requests. `gather` preserves order,
    so each summary lines up with its text and chunking stays deterministic (each call is independent
    of concurrency)."""
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
    token_cap: int = DEFAULT_TOKEN_CAP,
    max_concurrency: int = DEFAULT_SUMMARY_CONCURRENCY,
) -> ChunkManifest:
    """Chunk a parsed document into a `ChunkManifest`, content-hash gated (chunked once).

    If a manifest for this document's content hash already exists it is reused (no re-chunk);
    otherwise the parsed document is sliced, each slice is summarized (concurrently, bounded by
    `max_concurrency`), boundaries are validated, and the manifest is cached.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = cache_dir / f"{parsed.source_doc_id}.{parsed.content_hash[:16]}.chunks.json"
    if manifest_path.exists():  # content-hash gate
        return ChunkManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))

    document = load_document(parsed)
    texts = _split_into_chunks(document, token_cap)
    summaries = asyncio.run(_summarize_all(texts, summarizer, max_concurrency))
    chunks = [
        Chunk(
            chunk_id=ChunkId.of(parsed.source_doc_id, index, text).value,
            chunk_index=index,
            text=text,
            summary=summary,
            token_estimate=_estimate_tokens(text),
        )
        for index, (text, summary) in enumerate(zip(texts, summaries))
    ]
    _validate_boundaries(chunks, token_cap)

    manifest = ChunkManifest(
        source_doc_id=parsed.source_doc_id,
        content_hash=parsed.content_hash,
        token_cap=token_cap,
        chunks=chunks,
    )
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
