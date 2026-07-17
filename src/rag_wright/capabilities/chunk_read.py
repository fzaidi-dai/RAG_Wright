"""chunk_read (FR-Q, T38): the governed text-rehydration step between fusion and synthesis.

Fusion (FR-Q.4) produces a capped evidence set of `chunk_id`s; synthesis (FR-Q.5) extracts over full
chunk text. But the retrieval index does not hold the text (it is dense-over-summary), so the `chunk_id`s
must be rehydrated to their text first. `chunk_read` is that step: it reads the chunk-text sidecar (T40,
`store/chunk_text.py`) and returns the full text per `chunk_id`, in the requested order.

It is a governed, discovered, bound capability, not caller-side plumbing: the compiler binds it as an
in-process `function` node under the `chunk_read` slug, so rehydration is a real registered capability with
its own contract, discoverable by representative queries. It drops nothing — a `chunk_id` that cannot be
rehydrated (an orphan, or an id the sidecar never received) is a pipeline inconsistency, raised loud,
never a silent evidence drop (the same no-silent-drop discipline as FR-Q.6's no-claim-without-a-citation).
"""

from __future__ import annotations

from pydantic import BaseModel

from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.store.chunk_text import ChunkTextStore


class ChunkText(BaseModel):
    """One rehydrated chunk: its id, full text (from the T40 sidecar), and its source document.

    `source_doc_id` is provenance, derived from the `chunk_id` (`<source_doc_id>:<index>:<hash>`), so it
    travels with the text into synthesis without a second store read.
    """

    chunk_id: str
    text: str
    source_doc_id: str


class ChunkReadResult(BaseModel):
    """The rehydrated evidence for synthesis: full text per `chunk_id`, in the requested order."""

    chunks: list[ChunkText]


def chunk_read(chunk_ids: list[str], *, text_store: ChunkTextStore) -> ChunkReadResult:
    """Rehydrate `chunk_ids` to their full text via the sidecar, order-preserving, dropping nothing.

    A `chunk_id` with no persisted text raises `KeyError`: rehydration cannot silently drop evidence, so
    a chunk that retrieval surfaced but the sidecar cannot supply is surfaced as an error, not skipped.
    """
    chunks: list[ChunkText] = []
    for chunk_id in chunk_ids:
        text = text_store.get(chunk_id)
        if text is None:
            raise KeyError(
                f"chunk_read: no persisted text for chunk_id {chunk_id!r} — cannot rehydrate "
                "(orphaned chunk or an id the ingest sidecar never received); evidence is not dropped"
            )
        source_doc_id = chunk_id.rsplit(":", 2)[0]  # <source_doc_id>:<chunk_index>:<content_hash>
        chunks.append(ChunkText(chunk_id=chunk_id, text=text, source_doc_id=source_doc_id))
    return ChunkReadResult(chunks=chunks)


def register_chunk_read(registry: CapabilityRegistry) -> None:
    """Register chunk_read under FR-Q (`chunk_read`, an in-process `function`)."""
    registry.register(
        "chunk_read",
        contract=ChunkReadResult,
        kind="function",
        display_name="Chunk read (rehydrate chunk_ids to full text)",
    )
