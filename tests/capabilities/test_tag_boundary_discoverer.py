"""CHUNK-3 (ADR-0058, issue 0004): the tag-parse boundary discoverer + the flat cut-index contract. Hermetic --
an injected `structured_factory` returns a fixed `_CutIndices`, so no LLM / network. Proves the FLAT cut-index
list (fits tag_structured's scope) becomes a valid partition, is robust to a bad model list (no lost text), and
the model call goes through the tag-parse factory with the flat schema + a stage label."""

from __future__ import annotations

from rag_wright.capabilities.rlm_chunking import (
    TagBoundaryDiscoverer,
    _CutIndices,
    _cuts_to_spans,
    _validate_partition,
)


class _FakeRunnable:
    def __init__(self, cuts):
        self._cuts = cuts

    def invoke(self, _prompt):
        return _CutIndices(cuts=self._cuts)

    async def ainvoke(self, _prompt):
        return _CutIndices(cuts=self._cuts)


def _factory(cuts):
    return lambda _m, _s, **_kw: _FakeRunnable(cuts)


class _Item:
    def __init__(self, i):
        self.text = f"item {i}"
        self.label = None  # not a heading -- this discoverer uses the model, not labels


class _Doc:
    def __init__(self, n):
        self.texts = [_Item(i) for i in range(n)]


def _pairs(spans):
    return [(s.start_index, s.end_index) for s in spans]


def test_cut_indices_become_a_valid_partition():
    disc = TagBoundaryDiscoverer(model_id="m", structured_factory=_factory([0, 3, 7]))
    spans = disc.discover(_Doc(10))
    assert _pairs(spans) == [(0, 2), (3, 6), (7, 9)]


def test_cuts_to_spans_is_robust_to_a_bad_model_list():
    # unordered, out-of-range, duplicate, missing 0, negative -> still a valid partition, no lost text
    spans = _cuts_to_spans([7, 3, 3, 99, -1], 10)
    _validate_partition(spans, 10)  # raises if not contiguous/gap-free/covering
    assert _pairs(spans) == [(0, 2), (3, 6), (7, 9)]


def test_empty_document_returns_no_spans():
    disc = TagBoundaryDiscoverer(model_id="m", structured_factory=_factory([]))
    assert disc.discover(_Doc(0)) == []


async def test_adiscover_uses_the_tag_parse_factory():
    disc = TagBoundaryDiscoverer(model_id="m", structured_factory=_factory([0, 5]))
    assert _pairs(await disc.adiscover(_Doc(8))) == [(0, 4), (5, 7)]


def test_factory_receives_the_flat_cut_index_schema_and_a_stage_label():
    seen = {}

    def factory(model_id, schema, **kw):
        seen["schema"], seen["label"] = schema, kw.get("label")
        return _FakeRunnable([0, 4])

    TagBoundaryDiscoverer(model_id="m", structured_factory=factory).discover(_Doc(8))
    assert seen["schema"] is _CutIndices  # the FLAT contract (no nested list[BaseModel])
    assert seen["label"] == "semantic_chunking.discover"  # names the stage for the deadline warning
