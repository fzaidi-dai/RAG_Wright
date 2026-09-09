"""SPAN-CLAUSE-RERANK (b) (ADR-0033): property-boosted typed retrieval. Hermetic -- fake store + embedder."""

from __future__ import annotations

from rag_wright.capabilities.property_boosted_retrieval import RankedSpan, property_boosted_retrieval


class _FakeStore:
    """A tiny in-memory stand-in: a BGE-ordered pool per function + the span->props / span->text joins."""

    def __init__(self, pool, props, texts, scores=None):
        self._pool = pool  # {function: [span_id, ...] in BGE order}
        self._props = props  # {span_id: {(dim, value)}}
        self._texts = texts  # {span_id: text}
        self._scores = scores or {}  # {span_id: fused RRF score} (issue 0023); omitted -> no `score` key

    def span_hybrid_search(self, dense, sparse, *, k, function=None):
        hits = []
        for s in self._pool.get(function, [])[:k]:
            h = {"span_id": s, "function": function}
            if s in self._scores:
                h["score"] = self._scores[s]
            hits.append(h)
        return hits

    def span_properties(self, span_ids):
        return {s: set(self._props.get(s, set())) for s in span_ids}

    def span_texts(self, span_ids):
        return {s: self._texts.get(s, "") for s in span_ids}


class _FakeEmbedder:
    def encode_dense(self, text):
        return [0.0]

    def encode_sparse(self, text):
        return {0: 0.0}


def test_constraint_matching_spans_are_boosted_above_bge_order():
    # BGE order puts the matching span (s3) LAST; the property boost must lift it to rank 1
    store = _FakeStore(
        pool={"Anti-Assignment": ["s1", "s2", "s3"]},
        props={
            "s1": {("assignment_consent", "consent_required")},
            "s2": {("assignment_consent", "notice_only")},
            "s3": {("assignment_consent", "free")},  # the one the query wants
        },
        texts={"s1": "one", "s2": "two", "s3": "three"},
    )
    out = property_boosted_retrieval(
        "assignment", store=store, embedder=_FakeEmbedder(),
        functions=["Anti-Assignment"], constraints={("assignment_consent", "free")}, k=3)
    assert [r.span_id for r in out] == ["s3", "s1", "s2"]  # s3 boosted to rank 1; s1/s2 keep BGE order
    assert out[0] == RankedSpan(span_id="s3", text="three", function="Anti-Assignment",
                                match_score=1.0, matched=[("assignment_consent", "free")], rank=1)
    assert out[1].match_score == 0.0 and out[1].matched == []


def test_retrieval_score_is_carried_for_the_relevance_floor():
    # issue 0023: the fused RRF relevance that ordered the pool is carried on RankedSpan so a caller can apply its
    # OWN floor (matched/possible/not_found). match_score (constraint count) and retrieval_score are independent.
    store = _FakeStore(
        pool={None: ["s1", "s2"]},
        props={"s1": {("cap_basis", "multiple_of_fees")}, "s2": set()},
        texts={"s1": "one", "s2": "two"},
        scores={"s1": 0.031, "s2": 0.016},
    )
    out = property_boosted_retrieval(
        "cap", store=store, embedder=_FakeEmbedder(),
        functions=[], constraints={("cap_basis", "multiple_of_fees")}, k=2)
    by_id = {r.span_id: r for r in out}
    assert by_id["s1"].retrieval_score == 0.031 and by_id["s1"].match_score == 1.0  # matched AND its fused score
    assert by_id["s2"].retrieval_score == 0.016 and by_id["s2"].match_score == 0.0  # possible: retrieved, 0 match


def test_retrieval_score_is_none_when_the_store_omits_it():
    # a backend that surfaces no fused score -> retrieval_score None (Langfuse-style graceful absence), not a crash
    store = _FakeStore(pool={None: ["a"]}, props={"a": set()}, texts={"a": "A"})  # no scores
    out = property_boosted_retrieval("q", store=store, embedder=_FakeEmbedder(), functions=[], constraints=set(), k=1)
    assert out[0].retrieval_score is None


def test_bge_order_is_the_tiebreak_within_equal_match():
    # no constraints -> all match_score 0 -> pure BGE order preserved (stable)
    store = _FakeStore(
        pool={"F": ["a", "b", "c"]}, props={"a": set(), "b": set(), "c": set()},
        texts={"a": "A", "b": "B", "c": "C"})
    out = property_boosted_retrieval("q", store=store, embedder=_FakeEmbedder(),
                                     functions=["F"], constraints=set(), k=3)
    assert [r.span_id for r in out] == ["a", "b", "c"]


def test_pool_is_deduped_across_functions_keeping_first_bge_position():
    store = _FakeStore(
        pool={"F1": ["x", "y"], "F2": ["y", "z"]},  # y appears in both
        props={"x": set(), "y": set(), "z": set()}, texts={})
    out = property_boosted_retrieval("q", store=store, embedder=_FakeEmbedder(),
                                     functions=["F1", "F2"], constraints=set(), k=10)
    assert [r.span_id for r in out] == ["x", "y", "z"]  # y not duplicated


def test_empty_pool_returns_empty():
    store = _FakeStore(pool={}, props={}, texts={})
    assert property_boosted_retrieval("q", store=store, embedder=_FakeEmbedder(),
                                      functions=["F"], constraints=set()) == []
