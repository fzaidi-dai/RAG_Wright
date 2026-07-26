"""CU-C1: NL->type query understanding. Tests the boundary normalization (loose emit schema -> strict
QueryIntent) hermetically -- a fake reason_factory + structured_factory drive the two-step; no LLM, no network."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from rag_wright.capabilities.query_understanding import _RawIntent, understand_query
from rag_wright.contracts.function import canonical_function


def _reason_factory():
    """A reason_factory stand-in: returns a runnable whose .invoke(...).content is ignored by the fake emit."""

    class _R:
        def invoke(self, _prompt):
            return SimpleNamespace(content="TYPES: ...\nINTENT: ...\nVALUE: ...")

    return lambda _model: _R()


def _factory(raw):
    """A structured_factory (emit) stand-in: ignores model/schema, returns a runnable whose .invoke gives `raw`.
    `raw=None` simulates a failed forced structured emit."""

    class _Runnable:
        def invoke(self, _prompt):
            return raw

    return lambda _model, _schema: _Runnable()


def _understand(raw):
    return understand_query("q", reason_factory=_reason_factory(), structured_factory=_factory(raw))


def test_failed_emit_degrades_to_out_of_taxonomy_low_confidence():
    intent = _understand(None)  # forced structured emit returned None
    assert intent.clause_types == [] and intent.in_taxonomy is False and intent.confidence == 0.0


def test_single_in_taxonomy_type():
    intent = _understand(_RawIntent(clause_types=["Governing Law"]))
    assert intent.clause_types == ["Governing Law"]
    assert intent.in_taxonomy is True
    assert intent.intent == "highlight"


def test_labels_are_canonicalized_case_insensitively():
    intent = _understand(_RawIntent(clause_types=["governing law"]))
    assert intent.clause_types == ["Governing Law"]  # normalized to the canonical casing


def test_multi_type_allowed():
    intent = _understand(_RawIntent(clause_types=["Governing Law", "Insurance"]))
    assert intent.clause_types == ["Governing Law", "Insurance"]
    assert intent.in_taxonomy is True


def test_duplicate_labels_are_deduped_order_preserved():
    intent = _understand(_RawIntent(clause_types=["Insurance", "governing law", "Governing Law", "insurance"]))
    assert intent.clause_types == ["Insurance", "Governing Law"]


def test_out_of_taxonomy_empty_list_flags_fallback():
    intent = _understand(_RawIntent(clause_types=[], in_taxonomy=False))
    assert intent.clause_types == []
    assert intent.in_taxonomy is False  # -> serve does semantic fallback + low-confidence flag


def test_hallucinated_label_is_dropped_and_becomes_out_of_taxonomy():
    assert canonical_function("Zebra Clause") is None  # guard: genuinely not a taxonomy label
    # the LLM insists in_taxonomy=True, but nothing maps -> we DERIVE in_taxonomy=False (graceful fallback)
    intent = _understand(_RawIntent(clause_types=["Zebra Clause"], in_taxonomy=True))
    assert intent.clause_types == []
    assert intent.in_taxonomy is False


def test_partial_map_keeps_the_valid_types():
    intent = _understand(_RawIntent(clause_types=["Zebra Clause", "Governing Law"]))
    assert intent.clause_types == ["Governing Law"]
    assert intent.in_taxonomy is True


def test_extract_intent_keeps_value_to_extract():
    intent = _understand(_RawIntent(clause_types=["Governing Law"], intent="extract",
                                     value_to_extract="governing law state"))
    assert intent.intent == "extract"
    assert intent.value_to_extract == "governing law state"
    assert intent.value_condition is None


def test_discriminate_intent_keeps_value_condition():
    intent = _understand(_RawIntent(clause_types=["Insurance"], intent="discriminate",
                                     value_condition="the policy naming the buyer as additional insured"))
    assert intent.intent == "discriminate"
    assert intent.value_condition == "the policy naming the buyer as additional insured"
    assert intent.value_to_extract is None


def test_value_fields_nulled_when_intent_is_highlight():
    intent = _understand(_RawIntent(clause_types=["Governing Law"], intent="highlight",
                                     value_to_extract="leaked", value_condition="leaked"))
    assert intent.value_to_extract is None and intent.value_condition is None


def test_unknown_intent_defaults_to_highlight():
    intent = _understand(_RawIntent(clause_types=["Governing Law"], intent="frobnicate"))
    assert intent.intent == "highlight"


@pytest.mark.parametrize("raw_conf,expected", [(1.7, 1.0), (-0.2, 0.0), (0.63, 0.63)])
def test_confidence_is_clamped(raw_conf, expected):
    intent = _understand(_RawIntent(clause_types=["Governing Law"], confidence=raw_conf))
    assert intent.confidence == expected
