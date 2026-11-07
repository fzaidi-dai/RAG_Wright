"""PS-63 (gap G8): the ingestion default writer asks the store for idempotent edges, and `api.kg_write` passes the
choice through; a domain `writer=` is untouched."""
from __future__ import annotations

import asyncio

from rag_wright.api import KgEdge, KgNode, kg_write
from rag_wright.api.workspace import WorkspaceHandle

from tests.ingestion.test_write_count import _DOC, _EXTRACTIONS, _stages


class _Store:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def kg_write(self, nodes, edges=(), *, ensure_edges=False):
        self.calls.append({"nodes": list(nodes), "edges": list(edges), "ensure_edges": ensure_edges})


def test_the_default_writer_ensures_its_edges(tmp_path):
    store = _Store()
    asyncio.run(_stages(store=store, tmp_path=tmp_path).write(_DOC, _EXTRACTIONS))
    assert [c["ensure_edges"] for c in store.calls] == [True]


def test_api_kg_write_passes_the_choice_through():
    store = _Store()
    ws = WorkspaceHandle(store, None, "t")  # type: ignore[arg-type]
    edge = KgEdge("E", "T", "k", "1", "T", "k", "2", {})
    kg_write(ws, [KgNode("T", "k", {"k": "1"})], [edge])
    kg_write(ws, [], [edge], ensure_edges=True)
    assert [c["ensure_edges"] for c in store.calls] == [False, True]
