"""Fusion (T27, FR-Q.4): union/dedup the reranked set and graph-cited chunks on chunk_id, capped.

Hermetic: a deterministic union (retrieval order first, then graph), dedup with source tagging, a cap,
and no score (this is not a score fusion).
"""

from __future__ import annotations

from rag_wright.capabilities.fusion import fuse
from rag_wright.capabilities.graph_query import GraphAnswer, GraphEvidence
from rag_wright.capabilities.reranking import RerankResult, ScoredCandidate


def _reranked(*chunk_ids: str) -> RerankResult:
    return RerankResult(
        query="q",
        candidates=[ScoredCandidate(chunk_id=c, source_doc_id="docA", score=1.0) for c in chunk_ids],
    )


def _graph(*chunk_id_lists: list[str]) -> GraphAnswer:
    evidence = [
        GraphEvidence(entity_id=f"e{i}", name=f"E{i}", hops=1, path_entity_ids=["s", f"e{i}"],
                      chunk_ids=cids, confidences=["EXTRACTED"] * len(cids))
        for i, cids in enumerate(chunk_id_lists)
    ]
    return GraphAnswer(start_entity_id="s", relationship_type="Contracts With", evidence=evidence)


def test_unions_and_dedups_on_chunk_id_with_source_tags():
    result = fuse(_reranked("c1", "c2"), _graph(["c2", "c3"]))

    assert [c.chunk_id for c in result.chunks] == ["c1", "c2", "c3"]  # retrieval order first, then graph
    by_id = {c.chunk_id: c for c in result.chunks}
    assert by_id["c1"].sources == ["retrieval"]
    assert by_id["c2"].sources == ["graph", "retrieval"]  # surfaced by both, kept once
    assert by_id["c3"].sources == ["graph"]


def test_is_capped():
    result = fuse(_reranked("c1", "c2", "c3"), _graph(["c4", "c5"]), cap=2)
    assert [c.chunk_id for c in result.chunks] == ["c1", "c2"]  # union cut to the cap


def test_is_deterministic():
    a = fuse(_reranked("c1", "c2"), _graph(["c3"], ["c2"]))
    b = fuse(_reranked("c1", "c2"), _graph(["c3"], ["c2"]))
    assert a.model_dump() == b.model_dump()  # same inputs -> identical output


def test_is_not_a_score_fusion():
    result = fuse(_reranked("c1"), _graph(["c2"]))
    assert not any(hasattr(c, "score") for c in result.chunks)  # union, not a scored merge
    assert all(c.sources for c in result.chunks)


def test_handles_an_empty_stream_on_either_side():
    assert [c.chunk_id for c in fuse(_reranked("c1"), _graph()).chunks] == ["c1"]  # graph empty
    assert [c.chunk_id for c in fuse(_reranked(), _graph(["c2"])).chunks] == ["c2"]  # retrieval empty


# (EP-CORE-1a/ADR-0118: fusion is de-registered from ARD — a core helper now, not a capability; its
# registration test was removed. `fuse` is tested below as an ordinary function.)
