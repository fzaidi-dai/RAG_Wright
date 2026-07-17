"""Chunk write + incremental upsert (FR-I.3, FR-I.5): assemble chunk records and write them, gated.

This is the ingestion write leg: it assembles a `ChunkRecord` (T3) from a chunk (T17, summary +
text) and its embedding (T19, dense + sparse), then upserts it into the store by `chunk_id` (a
re-write updates in place, never duplicates). Ingestion is incremental, resumable, and idempotent: a
content-hash gate skips an unchanged document (effectively no work); per-document and per-chunk
checkpoints let a run resume where it stopped; and a document whose write fails lands in a
dead-letter queue.

Chunk write is a seam-bound pipeline node, not a capability discovered by representative queries, so
it registers nothing and authors no ARD manifest (SPEC section 5). It talks to the store only through
the swappable `Store` seam (T13), so it works against ArcadeDB or the LanceDB fallback unchanged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping, Optional

from rag_wright.capabilities.embedding import ChunkEmbedding
from rag_wright.capabilities.rlm_chunking import Chunk
from rag_wright.contracts.chunk import ChunkRecord, MetadataValue
from rag_wright.contracts.identifiers import ChunkId
from rag_wright.store.chunk_text import ChunkTextStore
from rag_wright.store.seam import Store

WriteStatus = Literal["written", "skipped", "dead_lettered"]


@dataclass(frozen=True)
class DocumentWriteResult:
    """The outcome of writing one document's chunks."""

    source_doc_id: str
    status: WriteStatus
    written_count: int
    error: Optional[str] = None


def to_chunk_record(
    chunk: Chunk,
    embedding: ChunkEmbedding,
    *,
    source_doc_id: str,
    source_metadata: Optional[dict[str, MetadataValue]] = None,
) -> ChunkRecord:
    """Assemble a `ChunkRecord` from a chunk and its embedding, checking the ids agree.

    The `chunk_id` is recomputed deterministically from `(source_doc_id, chunk_index, text)` (T1) and
    must match both the chunk's and the embedding's `chunk_id`. Keywords and entity mentions are empty
    here; they are added by the metadata/extraction stages, not the write.
    """
    chunk_id = ChunkId.of(source_doc_id, chunk.chunk_index, chunk.text)
    if chunk_id.value != chunk.chunk_id or embedding.chunk_id != chunk.chunk_id:
        raise ValueError(
            f"chunk_id mismatch: recomputed {chunk_id.value!r}, chunk {chunk.chunk_id!r}, "
            f"embedding {embedding.chunk_id!r}"
        )
    return ChunkRecord(
        chunk_id=chunk_id,
        summary=chunk.summary,
        dense_vector=embedding.dense_vector,
        sparse_vector=embedding.sparse_vector,
        source_metadata=source_metadata or {},
    )


class ChunkWriter:
    """Writes chunk records to the store, content-hash gated with checkpoints and a dead-letter queue.

    Checkpoints and the dead-letter queue are files under `checkpoint_dir`; the store holds the chunk
    records (summary + vectors). The full chunk text the index omits is persisted to `text_store`, the
    chunk-text sidecar (T40), under the SAME content-hash gate, so the index and the sidecar are driven
    by one decision and never diverge. On resume, chunks already recorded in a document's checkpoint are
    skipped for both.
    """

    def __init__(self, store: Store, *, text_store: ChunkTextStore, checkpoint_dir: Path) -> None:
        self._store = store
        self._text_store = text_store
        self._checkpoints = Path(checkpoint_dir) / "checkpoints"
        self._dead_letter = Path(checkpoint_dir) / "dead_letter"
        self._checkpoints.mkdir(parents=True, exist_ok=True)
        self._dead_letter.mkdir(parents=True, exist_ok=True)

    def write_document(
        self,
        source_doc_id: str,
        content_hash: str,
        records: list[ChunkRecord],
        *,
        texts: Mapping[str, str],
    ) -> DocumentWriteResult:
        """Write a document's chunk records, upserting by `chunk_id`. Gated, resumable, dead-lettered.

        `texts` maps each record's `chunk_id` to its full chunk text, persisted to the sidecar alongside
        the index upsert. A record without a matching text is rejected before any write, so a chunk can
        never land in the index without its text in the sidecar (the lifecycle-coupling guard).
        """
        missing = [r.chunk_id.value for r in records if r.chunk_id.value not in texts]
        if missing:
            raise ValueError(f"no sidecar text supplied for chunk_ids: {missing}")

        checkpoint = self._load_checkpoint(source_doc_id)
        same_content = checkpoint is not None and checkpoint["content_hash"] == content_hash
        if same_content and checkpoint["status"] == "complete":
            return DocumentWriteResult(source_doc_id, "skipped", 0)  # content-hash gate: no work

        written = set(checkpoint["written"]) if same_content else set()  # resume, or start fresh
        newly = 0
        try:
            for record in records:
                chunk_id = record.chunk_id.value
                if chunk_id in written:
                    continue  # already written on an earlier run (per-chunk checkpoint)
                self._store.upsert_chunk(record)
                self._text_store.put(record.chunk_id, texts[chunk_id])  # sidecar, same gate as the index
                written.add(chunk_id)
                newly += 1
                self._save_checkpoint(source_doc_id, content_hash, written, "in_progress")
        except Exception as exc:  # noqa: BLE001 — a failed document is dead-lettered, not raised
            self._save_checkpoint(source_doc_id, content_hash, written, "in_progress")
            self._write_dead_letter(source_doc_id, content_hash, str(exc))
            return DocumentWriteResult(source_doc_id, "dead_lettered", newly, error=str(exc))

        self._save_checkpoint(source_doc_id, content_hash, written, "complete")
        self._clear_dead_letter(source_doc_id)
        return DocumentWriteResult(source_doc_id, "written", newly)

    def dead_letter_ids(self) -> set[str]:
        """The source-doc ids currently in the dead-letter queue."""
        return {path.stem for path in self._dead_letter.glob("*.json")}

    # --- checkpoint / dead-letter files ---------------------------------------------------------

    def _checkpoint_path(self, source_doc_id: str) -> Path:
        return self._checkpoints / f"{source_doc_id}.json"

    def _load_checkpoint(self, source_doc_id: str) -> Optional[dict]:
        path = self._checkpoint_path(source_doc_id)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def _save_checkpoint(
        self, source_doc_id: str, content_hash: str, written: set[str], status: str
    ) -> None:
        self._checkpoint_path(source_doc_id).write_text(
            json.dumps(
                {"content_hash": content_hash, "written": sorted(written), "status": status},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def _write_dead_letter(self, source_doc_id: str, content_hash: str, error: str) -> None:
        (self._dead_letter / f"{source_doc_id}.json").write_text(
            json.dumps({"content_hash": content_hash, "error": error}, ensure_ascii=False),
            encoding="utf-8",
        )

    def _clear_dead_letter(self, source_doc_id: str) -> None:
        (self._dead_letter / f"{source_doc_id}.json").unlink(missing_ok=True)
