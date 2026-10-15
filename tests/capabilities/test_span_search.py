"""PS-45 (engine gap G24): a public hybrid span search. The engine's span index (dense + sparse BGE-M3, fused by RRF
in the store) was reachable only from inside the engine, so a domain's own retrieval capability fell back to dense
similarity over what it embedded itself. `span_search` runs the fused search, unions the top pure-dense spans and
guarantees them a place in the result (the dense floor, ADR-0104), and returns cited spans with their positions."""
from __future__ import annotations

import asyncio

import pytest

from rag_wright.capabilities.span_search import SpanHit, select_with_dense_floor, span_search


class _Embedder:
    def encode_dense(self, text):
        return [0.1, 0.2]

    def encode_sparse(self, text):
        return {7: 1.0}


class _Store:
    """Fused order s1..s5; the pure-dense leg's best is s9, which fusion missed."""

    def __init__(self, fused=("s1", "s2", "s3", "s4", "s5"), dense=("s9", "s1")):
        self.fused, self.dense, self.calls = list(fused), list(dense), []

    def span_hybrid_search(self, dense, sparse, *, k, primary_tag=None, documents=None):
        self.calls.append(("hybrid", k, primary_tag, documents))
        return [{"span_id": s, "parent_chunk_id": "c", "primary_tag": ""} for s in self.fused[:k]]

    def span_dense_search(self, dense, *, k, documents=None):
        self.calls.append(("dense", k, documents))
        return [{"span_id": s, "parent_chunk_id": "c", "primary_tag": ""} for s in self.dense[:k]]

    def kg_read(self, node_type, *, where, fields):
        return [{"span_id": s, "parent_chunk_id": "c", "span_index": 0, "text": f"text of {s}", "primary_tag": "",
                 "document_id": "d1", "doc_start": 10, "doc_end": 20, "pages": [1], "bbox": None}
                for s in where["span_id"]]


def test_it_returns_cited_spans_in_fused_order_with_the_dense_floor_guaranteed():
    hits = span_search("q", store=_Store(), embedder=_Embedder(), k=3, dense_floor=1)
    assert [h.span_id for h in hits] == ["s1", "s2", "s9"]  # s9 takes the last slot: the floor is guaranteed
    assert [h.rank for h in hits] == [1, 2, 3]
    assert hits[2].found_by == ("dense",) and hits[0].found_by == ("hybrid",)
    both = span_search("q", store=_Store(), embedder=_Embedder(), k=3, dense_floor=2)
    assert both[0].found_by == ("hybrid", "dense")  # s1 is in both legs
    assert (hits[0].text, hits[0].document_id, hits[0].doc_start, hits[0].doc_end) == ("text of s1", "d1", 10, 20)


def test_the_floor_can_be_turned_off():
    store = _Store()
    hits = span_search("q", store=store, embedder=_Embedder(), k=3, dense_floor=0)
    assert [h.span_id for h in hits] == ["s1", "s2", "s3"]
    assert all(c[0] == "hybrid" for c in store.calls)


def test_the_scope_and_the_tag_reach_the_store():
    store = _Store()
    span_search("q", store=store, embedder=_Embedder(), k=2, documents=["d1"], tag="finding")
    assert ("hybrid", 2, "finding", ["d1"]) in store.calls and ("dense", 3, ["d1"]) in store.calls


def test_an_empty_scope_returns_nothing_without_a_query():
    store = _Store()
    assert span_search("q", store=store, embedder=_Embedder(), documents=[]) == [] and store.calls == []


@pytest.mark.parametrize("k", [0, -1])
def test_k_must_be_positive(k):
    with pytest.raises(ValueError):
        span_search("q", store=_Store(), embedder=_Embedder(), k=k)


def test_select_with_dense_floor_reserves_slots():
    assert select_with_dense_floor(["a", "b", "c", "d"], ["d"], 2) == ["a", "d"]
    assert select_with_dense_floor(["a", "b"], [], 5) == ["a", "b"]


def test_the_public_call_uses_the_workspace_store_and_embedder(monkeypatch):
    from rag_wright import api

    class _Ws:
        _store, embedder = _Store(), _Embedder()

    hits = api.span_search(_Ws(), "q", k=2, dense_floor=0)
    assert isinstance(hits[0], SpanHit) and [h.span_id for h in hits] == ["s1", "s2"]
    assert [h.span_id for h in asyncio.run(api.aspan_search(_Ws(), "q", k=1, dense_floor=0))] == ["s1"]


def test_a_workspace_without_an_embedder_is_refused():
    from rag_wright import api

    class _Ws:
        _store, embedder = _Store(), None

    with pytest.raises(RuntimeError, match="embedder"):
        api.span_search(_Ws(), "q")


@pytest.mark.store
@pytest.mark.embed
def test_live_span_search_over_a_real_workspace():
    """Real ArcadeDB, real BGE-M3: spans indexed the way ingestion indexes them, found by meaning, scoped and cited."""
    from rag_wright.api import EngineConfig, StoreConfig, temporary_workspace
    from rag_wright.contracts.span import SpanRecord

    texts = {
        "d1": ["The fabric shrank by four percent after the second wash.",
               "Tensile strength in the warp direction was 410 newtons."],
        "d2": ["The dye lot showed uneven colour across the selvedge.",
               "Shipment was delayed by two weeks at the port."],
    }
    with temporary_workspace(EngineConfig(store=StoreConfig.from_env()), prefix="spansearch") as ws:
        for doc, spans in texts.items():
            dense, sparse = ws.embedder.encode_batch(spans)
            for i, (text, d, sp) in enumerate(zip(spans, dense, sparse)):
                ws._store.upsert_span(SpanRecord(
                    span_id=f"{doc}:0:h#{i}", parent_chunk_id=f"{doc}:0:h", span_index=i, text=text,
                    dense_vector=list(d), sparse_vector=sp, document_id=doc, doc_start=i * 100,
                    doc_end=i * 100 + len(text)))
        from rag_wright.api import span_search

        hits = span_search(ws, "how much did the cloth shrink when washed", k=2)
        assert hits[0].span_id == "d1:0:h#0" and hits[0].document_id == "d1" and hits[0].rank == 1
        assert hits[0].text.startswith("The fabric shrank") and hits[0].doc_end > hits[0].doc_start
        scoped = span_search(ws, "how much did the cloth shrink when washed", k=2, documents=["d2"])
        assert scoped and {h.document_id for h in scoped} == {"d2"}
