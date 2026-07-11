"""Hybrid search (T21, FR-C.3/FR-Q.1): RRF fusion of the dense and sparse legs, with metadata filters.

Hermetic tests use a fake embedder and a recording fake store to prove the capability embeds the
query once (dense and sparse over the query text), hands both query vectors and the filters to the
store's server-side fusion, and returns the ranked candidate list in store order. The live `-m store`
test runs the real ArcadeDB RRF fusion end to end over the T13 schema (the SQL T14 proved), driving it
through the capability with a fake embedder that supplies the query vectors (no BGE-M3 needed).
"""

from __future__ import annotations

import pytest

from rag_wright.capabilities.hybrid_search import (
    Candidate,
    HybridSearchResult,
    hybrid_search,
    register_hybrid_search,
)
from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.contracts.chunk import BGE_M3_DENSE_DIM, ChunkRecord
from rag_wright.contracts.identifiers import ChunkId
from rag_wright.store.arcadedb import ArcadeDBStore


class _FakeEmbedder:
    """Records what text it embedded and returns fixed query vectors (the T19 `Embedder` shape)."""

    def __init__(self, dense: list[float], sparse: dict[int, float]) -> None:
        self._dense, self._sparse = dense, sparse
        self.dense_inputs: list[str] = []
        self.sparse_inputs: list[str] = []

    def encode_dense(self, text: str) -> list[float]:
        self.dense_inputs.append(text)
        return self._dense

    def encode_sparse(self, text: str) -> dict[int, float]:
        self.sparse_inputs.append(text)
        return self._sparse


class _RecordingStore:
    """A fake store that records the `hybrid_search` call and returns canned, pre-ranked rows."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.calls: list[dict] = []

    def hybrid_search(self, dense_query, sparse_query, *, k, filters=None):
        self.calls.append(
            {"dense": dense_query, "sparse": sparse_query, "k": k, "filters": filters}
        )
        return self._rows[:k]


# --- hermetic ------------------------------------------------------------------------------------


def test_embeds_the_query_once_dense_and_sparse_over_the_query_text():
    emb = _FakeEmbedder(dense=[0.1, 0.2], sparse={7: 0.9})
    store = _RecordingStore([{"chunk_id": "c1", "source_doc_id": "docA"}])

    hybrid_search("who are the parties", store=store, embedder=emb, k=5)

    assert emb.dense_inputs == ["who are the parties"]  # dense leg over the query text
    assert emb.sparse_inputs == ["who are the parties"]  # sparse leg over the same query text
    assert store.calls[0]["dense"] == [0.1, 0.2]
    assert store.calls[0]["sparse"] == {7: 0.9}
    assert store.calls[0]["k"] == 5


def test_returns_ranked_candidates_in_store_order():
    emb = _FakeEmbedder([0.0], {1: 1.0})
    rows = [
        {"chunk_id": "c1", "source_doc_id": "docA"},
        {"chunk_id": "c2", "source_doc_id": "docB"},
    ]

    result = hybrid_search("q", store=_RecordingStore(rows), embedder=emb, k=10)

    assert isinstance(result, HybridSearchResult)
    assert result.query == "q"
    # the server-side RRF order is preserved; the capability does not re-rank
    assert [c.chunk_id for c in result.candidates] == ["c1", "c2"]
    assert result.candidates[0] == Candidate(chunk_id="c1", source_doc_id="docA")


def test_metadata_filters_pass_through_to_the_store():
    emb = _FakeEmbedder([0.0], {1: 1.0})
    store = _RecordingStore([{"chunk_id": "c1", "source_doc_id": "docA"}])

    hybrid_search("q", store=store, embedder=emb, k=3, filters={"source_doc_id": "docA"})

    assert store.calls[0]["filters"] == {"source_doc_id": "docA"}


def test_k_caps_the_candidate_list():
    emb = _FakeEmbedder([0.0], {1: 1.0})
    rows = [{"chunk_id": f"c{i}", "source_doc_id": "docA"} for i in range(5)]

    result = hybrid_search("q", store=_RecordingStore(rows), embedder=emb, k=2)

    assert len(result.candidates) == 2


def test_registers_under_fr_c_3():
    registry = CapabilityRegistry()
    register_hybrid_search(registry)

    reg = registry.get("hybrid_search")
    assert reg.name == "hybrid_search"
    assert reg.contract is HybridSearchResult
    assert reg.kind == "function"


# --- live ArcadeDB (opt-in): the real server-side RRF fusion ------------------------------------

_DB = "ragwright_hybrid_search_test"


def _dense(*nonzero: tuple[int, float]) -> list[float]:
    """A BGE-M3-length dense vector with a few non-zero positions (keeps the literal small)."""
    v = [0.0] * BGE_M3_DENSE_DIM
    for i, w in nonzero:
        v[i] = w
    return v


# Four records across two source docs (the T14 design): dense favours axis 0 (c1 exact, c2 close);
# the sparse query token set {10,30} matches c2 exactly and c1/c4 partially. So c1 wins the dense
# leg, c2 wins the sparse leg, and c3 (weak on both) sinks under fusion.
_ROWS = [
    ("c1", "docA", _dense((0, 1.0)), {10: 0.9, 20: 0.3}),
    ("c2", "docA", _dense((0, 0.8), (1, 0.2)), {10: 0.8, 30: 0.6}),
    ("c3", "docB", _dense((1, 1.0)), {20: 0.7, 40: 0.5}),
    ("c4", "docB", _dense((2, 1.0)), {30: 0.9, 40: 0.2}),
]

_DENSE_Q = _dense((0, 1.0))
_SPARSE_Q = {10: 0.7, 30: 0.7}


def _cid(doc: str, label: str) -> str:
    """The canonical chunk_id the fixture writes for a labelled row (records go through upsert_chunk,
    so the stored id is `ChunkId.of(...)`'s value, not the short label)."""
    return ChunkId.of(doc, int(label[1:]), label).value


@pytest.fixture
def live_store():
    store = ArcadeDBStore.from_env(database=_DB, reset=True)
    store.ensure_schema()
    for chunk_id, doc, dense, sparse in _ROWS:
        store.upsert_chunk(
            ChunkRecord(
                chunk_id=ChunkId.of(doc, int(chunk_id[1:]), chunk_id),
                summary=f"summary of {chunk_id}",
                dense_vector=dense,
                sparse_vector=sparse,
            )
        )
    yield store
    store.drop()
    store.close()


@pytest.mark.store
def test_live_hybrid_search_returns_a_sensible_rrf_ranking(live_store):
    # a fake embedder that turns the query string into the intended query vectors (no BGE-M3 needed)
    emb = _FakeEmbedder(_DENSE_Q, _SPARSE_Q)

    result = hybrid_search("contract parties", store=live_store, embedder=emb, k=4)
    ids = [c.chunk_id for c in result.candidates]

    assert ids  # server-side RRF fusion returned a ranked list
    # c1 wins the dense leg and c2 the sparse leg; both fuse above the weak docB records
    assert set(ids[:2]) == {_cid("docA", "c1"), _cid("docA", "c2")}
    assert _cid("docB", "c3") not in ids[:2]


@pytest.mark.store
def test_live_hybrid_search_honors_a_metadata_filter(live_store):
    emb = _FakeEmbedder(_DENSE_Q, _SPARSE_Q)

    result = hybrid_search(
        "contract parties", store=live_store, embedder=emb, k=10, filters={"source_doc_id": "docA"}
    )

    assert result.candidates  # the filter did not empty the result
    assert {c.source_doc_id for c in result.candidates} == {"docA"}  # only the filtered source
    assert {c.chunk_id for c in result.candidates} <= {_cid("docA", "c1"), _cid("docA", "c2")}
