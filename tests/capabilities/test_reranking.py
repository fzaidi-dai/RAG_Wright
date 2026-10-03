"""Reranking (T22, FR-C.4/FR-Q.2): the cross-encoder precision gate over the retrieved candidates.

Hermetic tests use a stub reranker (scores keyed by passage text) to prove the capability scores every
(query, passage) pair, sorts by score descending, cuts to top_k, and preserves fused order on ties. The
live `-m rerank` test runs the real BGE-reranker cross-encoder and checks it ranks a clearly relevant
passage above an irrelevant one for a query.
"""

from __future__ import annotations

import pytest

from rag_wright.capabilities.reranking import (
    BGEReranker,
    Passage,
    RerankResult,
    ScoredCandidate,
    rerank,
)


class _StubReranker:
    """Scores a passage by a fixed lookup on its text; records what it was asked to score."""

    def __init__(self, scores_by_text: dict[str, float]) -> None:
        self._scores = scores_by_text
        self.seen_query: str | None = None
        self.seen_passages: list[str] = []

    def score(self, query: str, passages: list[str]) -> list[float]:
        self.seen_query = query
        self.seen_passages = list(passages)
        return [self._scores[p] for p in passages]


def _passage(cid: str, text: str, doc: str = "docA") -> Passage:
    return Passage(chunk_id=cid, source_doc_id=doc, text=text)


# --- hermetic ------------------------------------------------------------------------------------


def test_scores_every_query_passage_pair():
    reranker = _StubReranker({"alpha": 0.1, "beta": 0.9})
    passages = [_passage("c1", "alpha"), _passage("c2", "beta")]

    rerank("my query", passages, reranker=reranker, top_k=5)

    assert reranker.seen_query == "my query"
    assert reranker.seen_passages == ["alpha", "beta"]  # every candidate's text, in order


def test_reranks_by_score_descending_and_returns_scored_candidates():
    reranker = _StubReranker({"weak": 0.2, "strong": 0.95, "mid": 0.5})
    passages = [_passage("c1", "weak"), _passage("c2", "strong"), _passage("c3", "mid")]

    result = rerank("q", passages, reranker=reranker, top_k=5)

    assert isinstance(result, RerankResult)
    assert result.query == "q"
    assert [c.chunk_id for c in result.candidates] == ["c2", "c3", "c1"]  # by score desc
    assert result.candidates[0] == ScoredCandidate(chunk_id="c2", source_doc_id="docA", score=0.95)


def test_cuts_to_top_k():
    reranker = _StubReranker({f"p{i}": float(i) for i in range(5)})
    passages = [_passage(f"c{i}", f"p{i}") for i in range(5)]

    result = rerank("q", passages, reranker=reranker, top_k=2)

    assert len(result.candidates) == 2
    assert [c.chunk_id for c in result.candidates] == ["c4", "c3"]  # the two highest


def test_top_k_larger_than_candidate_count_returns_all():
    reranker = _StubReranker({"a": 0.3, "b": 0.7})
    passages = [_passage("c1", "a"), _passage("c2", "b")]

    result = rerank("q", passages, reranker=reranker, top_k=10)

    assert len(result.candidates) == 2


def test_ties_preserve_incoming_fused_order():
    reranker = _StubReranker({"x": 0.5, "y": 0.5, "z": 0.5})
    passages = [_passage("c1", "x"), _passage("c2", "y"), _passage("c3", "z")]

    result = rerank("q", passages, reranker=reranker, top_k=3)

    # equal scores keep the fused order they came in with (stable sort)
    assert [c.chunk_id for c in result.candidates] == ["c1", "c2", "c3"]


def test_empty_candidate_list_reranks_to_empty():
    reranker = _StubReranker({})

    result = rerank("q", [], reranker=reranker, top_k=5)

    assert result.query == "q"
    assert result.candidates == []


def test_mismatched_score_count_is_rejected():
    class _BadReranker:
        def score(self, query: str, passages: list[str]) -> list[float]:
            return [0.1]  # one score for two passages

    with pytest.raises(ValueError, match="1:1"):
        rerank("q", [_passage("c1", "a"), _passage("c2", "b")], reranker=_BadReranker(), top_k=5)


# (EP-CORE-1a/ADR-0118: reranking is de-registered from ARD — a core primitive now; registration test removed.)


# --- live BGE-reranker (opt-in) ------------------------------------------------------------------


@pytest.mark.rerank
def test_live_bge_reranker_ranks_relevant_above_irrelevant():
    reranker = BGEReranker()  # downloads BAAI/bge-reranker-v2-m3 on first run
    passages = [
        _passage("c_irrelevant", "The mitochondria is the powerhouse of the cell."),
        _passage(
            "c_relevant",
            "This agreement may be terminated by either party upon 30 days written notice.",
        ),
    ]

    result = rerank("How can this contract be terminated?", passages, reranker=reranker, top_k=2)

    assert result.candidates[0].chunk_id == "c_relevant"  # the cross-encoder puts the on-topic first
    assert result.candidates[0].score > result.candidates[1].score


# --- engine issue 0016: the shared reranker model must be serialized across threads too ---

def test_bge_reranker_compute_score_is_serialized_across_threads():
    import concurrent.futures
    import threading
    import time

    class _Probe:
        def __init__(self):
            self._l = threading.Lock()
            self.n = 0
            self.max_in_flight = 0

        def compute_score(self, pairs):
            with self._l:
                self.n += 1
                self.max_in_flight = max(self.max_in_flight, self.n)
            time.sleep(0.003)
            with self._l:
                self.n -= 1
            return [0.5 for _ in pairs]

    probe = _Probe()
    rr = BGEReranker(model=probe)  # injected fake -> no real model load
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for f in [ex.submit(rr.score, "q", ["p1", "p2"]) for _ in range(40)]:
            f.result()
    assert probe.max_in_flight == 1  # issue 0016: the instance lock serialized compute_score across threads
