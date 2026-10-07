"""SPAN-CLAUSE-RERANK (b) (ADR-0033): property-boosted typed retrieval. Hermetic -- fake store + embedder."""

from __future__ import annotations

from rag_wright.packs.contracts.schemas.value_match import constraint_match_count  # injected matcher (EP-CORE-1b)
from rag_wright.packs.contracts.capabilities.property_boosted_retrieval import RankedSpan, property_boosted_retrieval


class _FakeStore:
    """A tiny in-memory stand-in: a BGE-ordered pool per function + the span->props / span->text joins.
    `dense` (issue 0041) is the pure-dense-ordered span list (the dense floor)."""

    def __init__(self, pool, props, texts, dense=None):
        self._pool = pool  # {function: [span_id, ...] in RRF order}
        self._props = props  # {span_id: {(dim, value)}}
        self._texts = texts  # {span_id: text}
        self._dense = dense or []  # [span_id, ...] in pure-dense (cosine) order

    def span_hybrid_search(self, dense, sparse, *, k, primary_tag=None, documents=None):
        return [{"span_id": s, "primary_tag": primary_tag} for s in self._pool.get(primary_tag, [])[:k]]

    def span_dense_search(self, dense, *, k, documents=None):  # issue 0041: pure-dense floor
        return [{"span_id": s, "primary_tag": ""} for s in self._dense[:k]]

    def kg_edges(self, from_type=None, *, where=None, key_range=None, direction="out", edge_type=None,
                 edge_where=None, target_where=None, select):
        # EP-REF-1a-ii: the typed-prop read now goes store.kg_edges (edge scan); ContractKGStore.span_properties
        # aggregates these rows. Return the seeded (dimension, value) rows for the requested span_ids.
        ids = set((edge_where or {}).get("span_id", []))
        return [{"span_id": s, "dimension": d, "value": v} for s in ids for (d, v) in self._props.get(s, set())]

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
        functions=["Anti-Assignment"], constraints={("assignment_consent", "free")}, k=3, match_count_fn=constraint_match_count)
    assert [r.span_id for r in out] == ["s3", "s1", "s2"]  # s3 boosted to rank 1; s1/s2 keep BGE order
    assert out[0] == RankedSpan(span_id="s3", text="three", function="Anti-Assignment",
                                match_score=1.0, matched=[("assignment_consent", "free")], rank=1)
    assert out[1].match_score == 0.0 and out[1].matched == []


def test_bge_order_is_the_tiebreak_within_equal_match():
    # no constraints -> all match_score 0 -> pure BGE order preserved (stable)
    store = _FakeStore(
        pool={"F": ["a", "b", "c"]}, props={"a": set(), "b": set(), "c": set()},
        texts={"a": "A", "b": "B", "c": "C"})
    out = property_boosted_retrieval("q", store=store, embedder=_FakeEmbedder(),
                                     functions=["F"], constraints=set(), k=3, match_count_fn=constraint_match_count)
    assert [r.span_id for r in out] == ["a", "b", "c"]


def test_pool_is_deduped_across_functions_keeping_first_bge_position():
    store = _FakeStore(
        pool={"F1": ["x", "y"], "F2": ["y", "z"]},  # y appears in both
        props={"x": set(), "y": set(), "z": set()}, texts={})
    out = property_boosted_retrieval("q", store=store, embedder=_FakeEmbedder(),
                                     functions=["F1", "F2"], constraints=set(), k=10, match_count_fn=constraint_match_count)
    assert [r.span_id for r in out] == ["x", "y", "z"]  # y not duplicated


def test_empty_pool_returns_empty():
    store = _FakeStore(pool={}, props={}, texts={})
    assert property_boosted_retrieval("q", store=store, embedder=_FakeEmbedder(),
                                      functions=["F"], constraints=set(), match_count_fn=constraint_match_count) == []


# --- issue 0041: dense floor-protection. A strong dense match must SURVIVE into the returned k even when the
# RRF fusion buries it (a short query on a ubiquitous token lets the sparse leg crowd it out). Fusion still
# decides ORDER; the top-N pure-dense spans are guaranteed MEMBERSHIP of the returned set. ---------------------
def test_dense_floor_span_absent_from_rrf_pool_is_guaranteed_into_k():
    # 'd1' is the true dense #1 but the RRF pool is all generic common-token spans (r1..r3). Without the floor
    # d1 is absent; with it, d1 is guaranteed into the returned k (a reserved slot), RRF order kept otherwise.
    store = _FakeStore(
        pool={None: ["r1", "r2", "r3"]},          # whole-index RRF pool (functions=()) -- d1 not present
        props={s: set() for s in ("r1", "r2", "r3", "d1")},
        texts={"d1": "the real payment-terms clause"},
        dense=["d1"],                              # pure-dense #1
    )
    out = property_boosted_retrieval("payment terms", store=store, embedder=_FakeEmbedder(),
                                     functions=(), constraints=set(), k=3, dense_floor_n=5, match_count_fn=constraint_match_count)
    ids = [r.span_id for r in out]
    assert "d1" in ids                             # guaranteed present (was absent before 0041)
    assert ids == ["r1", "r2", "d1"]               # RRF order kept; d1 fills the reserved tail slot


def test_dense_floor_does_not_disturb_a_query_where_dense_is_already_top():
    # the 5/7 working case: dense #1 is already high in RRF -> the floor is a no-op (no regression).
    store = _FakeStore(
        pool={None: ["d1", "r1", "r2"]}, props={s: set() for s in ("d1", "r1", "r2")},
        texts={}, dense=["d1"])
    out = property_boosted_retrieval("q", store=store, embedder=_FakeEmbedder(),
                                     functions=(), constraints=set(), k=3, dense_floor_n=5, match_count_fn=constraint_match_count)
    assert [r.span_id for r in out] == ["d1", "r1", "r2"]  # unchanged


def test_dense_floor_never_displaces_a_constraint_match():
    # a constraint-matching span outranks everything; the dense floor fills only non-matching tail slots.
    store = _FakeStore(
        pool={None: ["m1", "r1"]},                 # m1 matches the constraint; r1 does not
        props={"m1": {("mutuality", "mutual")}, "r1": set(), "d1": set()},
        texts={}, dense=["d1"])
    out = property_boosted_retrieval("q", store=store, embedder=_FakeEmbedder(),
                                     functions=(), constraints={("mutuality", "mutual")}, k=2, dense_floor_n=5, match_count_fn=constraint_match_count)
    ids = [r.span_id for r in out]
    assert ids[0] == "m1"                          # constraint match kept at rank 1
    assert "d1" in ids and "r1" not in ids         # dense floor took the non-matching slot, not the match
