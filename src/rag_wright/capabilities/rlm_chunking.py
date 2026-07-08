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

from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from rag_wright.capabilities.parsing import ParsedDocument, load_document
from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.contracts.identifiers import ChunkId
from rag_wright.models.profiles import ModelRole, model_for
from rag_wright.models.seam import build_structured

DEFAULT_TOKEN_CAP = 20_000
_HEADER_LABELS = ("section_header", "title")  # a heading starts a new chunk (section boundary)
_SUMMARIZE_PROMPT = "Summarize this contract chunk in one or two sentences, factually:"


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

    A new chunk starts at each section heading and whenever the token cap would be exceeded; a single
    over-cap item is hard-split so every chunk honors the cap. The cap is checked against the actual
    *joined* length (the `\\n\\n` separators plus the exact character count), so it matches each
    chunk's recomputed `token_estimate` and no chunk can slip over the cap through per-item rounding.
    """
    cap_chars = token_cap * 4  # _estimate_tokens is len // 4, so the cap in characters
    chunks: list[str] = []
    buffer: list[str] = []
    buffer_len = 0  # len(_SEP.join(buffer))

    def flush() -> None:
        nonlocal buffer, buffer_len
        if buffer:
            chunks.append(_SEP.join(buffer))
            buffer, buffer_len = [], 0

    for item in document.texts:
        text = (getattr(item, "text", "") or "").strip()
        if not text:
            continue
        label = str(getattr(item, "label", "")).lower()
        if any(h in label for h in _HEADER_LABELS):
            flush()  # section boundary

        if len(text) > cap_chars:  # a single item larger than the cap
            flush()
            chunks.extend(_hard_split(text, token_cap))
            continue

        added = (len(_SEP) if buffer else 0) + len(text)
        if buffer and buffer_len + added > cap_chars:  # cap boundary (measured on the joined text)
            flush()
            added = len(text)
        buffer.append(text)
        buffer_len += added

    flush()
    return chunks


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
        if chunk_.token_estimate > token_cap:
            raise BoundaryValidationError(f"chunk {index} exceeds token cap ({token_cap})")


def chunk(
    parsed: ParsedDocument,
    *,
    summarizer: Summarizer,
    cache_dir: Path,
    token_cap: int = DEFAULT_TOKEN_CAP,
) -> ChunkManifest:
    """Chunk a parsed document into a `ChunkManifest`, content-hash gated (chunked once).

    If a manifest for this document's content hash already exists it is reused (no re-chunk);
    otherwise the parsed document is sliced, each slice is summarized, boundaries are validated, and
    the manifest is cached.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = cache_dir / f"{parsed.source_doc_id}.{parsed.content_hash[:16]}.chunks.json"
    if manifest_path.exists():  # content-hash gate
        return ChunkManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))

    document = load_document(parsed)
    chunks = [
        Chunk(
            chunk_id=ChunkId.of(parsed.source_doc_id, index, text).value,
            chunk_index=index,
            text=text,
            summary=summarizer.summarize(text),
            token_estimate=_estimate_tokens(text),
        )
        for index, text in enumerate(_split_into_chunks(document, token_cap))
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
