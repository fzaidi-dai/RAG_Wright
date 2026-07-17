"""The chunk-text sidecar (T40, FR-I.3): the per-chunk full text the retrieval index omits.

The index is dense-over-summary by design: `ChunkRecord` holds the summary and the vectors, never the
raw chunk text (the text is used once to compute the `chunk_id` content hash, then discarded). But
synthesis (FR-Q.5) extracts over full chunk text, so the text must be persisted somewhere keyed by
`chunk_id` and rehydrated at query time by `chunk_read` (T38). This is that store: one JSON file per
source document, mapping the canonical `chunk_id` string to its full text.

It is deliberately NOT part of the swappable `Store` seam (ArcadeDB / LanceDB): it holds no vectors and
answers no query, it only round-trips text by id, so it does not belong behind the retrieval seam. Its
lifecycle is coupled to the chunk lifecycle: written at chunk write under the same content-hash gate as
the index upsert (so the two never diverge), and deleted per `source_doc_id` when a document is
re-chunked (`delete_document`, the T34 seam), so stale text is not orphaned relative to the index.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

from rag_wright.contracts.identifiers import ChunkId


class ChunkTextStore:
    """A per-source-document JSON sidecar mapping `chunk_id` -> full chunk text (T40, FR-I.3)."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def put(self, chunk_id: ChunkId, text: str) -> None:
        """Persist `text` for `chunk_id`, upserting by id under the document's sidecar file.

        Integrity check: the `chunk_id` already carries the content hash of its text (the same hash
        `ChunkId.of` computed at chunking, over these exact bytes), so `text` MUST hash to it. This is
        the sidecar's core promise made provable — a mismatch is a silent-wrong-text bug (a loop index
        error, a mismatched map) that would otherwise surface only as synthesis citing a valid-looking
        but wrong `chunk_id`; it is rejected here at the boundary, before it can be persisted.
        """
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest != chunk_id.content_hash:
            raise ValueError(
                f"chunk-text integrity: text for {chunk_id.value!r} hashes to {digest!r}, "
                f"not the chunk_id's content_hash {chunk_id.content_hash!r}"
            )
        path = self._path(chunk_id.source_doc_id)
        mapping = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        mapping[chunk_id.value] = text
        path.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")

    def get(self, chunk_id: str) -> Optional[str]:
        """The persisted text for `chunk_id` (canonical string form), or None if absent."""
        source_doc_id = chunk_id.rsplit(":", 2)[0]  # <source_doc_id>:<chunk_index>:<content_hash>
        path = self._path(source_doc_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8")).get(chunk_id)

    def delete_document(self, source_doc_id: str) -> None:
        """Remove all persisted text for a document (the T34 delete-and-re-chunk seam). Idempotent."""
        self._path(source_doc_id).unlink(missing_ok=True)

    def _path(self, source_doc_id: str) -> Path:
        return self._root / f"{source_doc_id}.json"
