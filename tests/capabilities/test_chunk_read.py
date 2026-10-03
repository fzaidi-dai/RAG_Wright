"""T38: chunk_read — the governed text-rehydration capability (FR-Q).

Between fusion (FR-Q.4) and synthesis (FR-Q.5) the retrieved `chunk_id`s must be rehydrated to their full
text, which the retrieval index does not hold (it is dense-over-summary). `chunk_read` reads the T40
chunk-text sidecar and returns text per `chunk_id`, in the requested order, dropping nothing — a `chunk_id`
that cannot be rehydrated is a pipeline inconsistency, raised loud, never a silent evidence drop. It
registers as a real FR-Q capability (a discovered, bound node, not caller-side plumbing).
"""

from __future__ import annotations

import pytest

from rag_wright.capabilities.chunk_read import (
    ChunkReadResult,
    chunk_read,
)
from rag_wright.contracts.identifiers import ChunkId
from rag_wright.store.chunk_text import ChunkTextStore


def _put(store: ChunkTextStore, source_doc_id: str, index: int, text: str) -> str:
    cid = ChunkId.of(source_doc_id, index, text)
    store.put(cid, text)
    return cid.value


def test_rehydrates_chunk_ids_to_text_and_provenance_in_order(tmp_path):
    store = ChunkTextStore(tmp_path)
    id0 = _put(store, "contract_a", 0, "the first clause, governed by Delaware law")
    id1 = _put(store, "contract_b", 3, "the second clause, effective on closing")

    result = chunk_read([id1, id0], text_store=store)  # requested order preserved (id1 first)

    assert isinstance(result, ChunkReadResult)
    assert [c.chunk_id for c in result.chunks] == [id1, id0]
    assert result.chunks[0].text == "the second clause, effective on closing"
    assert result.chunks[0].source_doc_id == "contract_b"  # provenance derived from the chunk_id
    assert result.chunks[1].source_doc_id == "contract_a"


def test_missing_text_raises_never_silently_drops(tmp_path):
    store = ChunkTextStore(tmp_path)
    present = _put(store, "contract_a", 0, "present clause")
    absent = "contract_a:9:" + "0" * 64  # a chunk_id that was never persisted

    with pytest.raises(KeyError):
        chunk_read([present, absent], text_store=store)


def test_empty_input_yields_empty_result(tmp_path):
    store = ChunkTextStore(tmp_path)
    assert chunk_read([], text_store=store).chunks == []


# (EP-CORE-1a/ADR-0118: chunk_read is de-registered from ARD — a core primitive now; registration test removed.)
