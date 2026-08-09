"""INGEST-LLM-CLASSIFIER (ADR-0048): the clause-level function classifier seam. Hermetic -- the LLM structured
runnable is injected, no real model. Covers post-processing (canonicalize/floor/cap), the LegalBERT back-compat
adapter, graceful degrade, and the batched `classify_spans` (option B) with INDEX-based alignment + sub-batching."""

from __future__ import annotations

from rag_wright.contracts.function import FunctionConfidence
from rag_wright.spans.clause_function_classifier import (
    BatchSpanClassification,
    ClauseFunctionClassification,
    LegalBertClauseAdapter,
    LlmBatchClauseClassifier,
    LlmClauseClassifier,
    RawScore,
    SpanFunctions,
)


class _FakeRunnable:
    def __init__(self, out):
        self._out = out

    def invoke(self, _prompt):
        return self._out


def _cls(scores):
    return ClauseFunctionClassification(functions=[RawScore(function=f, confidence=c) for f, c in scores])


# --- single-clause classify -----------------------------------------------------------------------------------


def test_llm_classifier_keeps_ranked_canonicalized_scores_above_floor():
    out = _cls([("cap on liability", "high"), ("Indemnification", "medium")])
    scores = LlmClauseClassifier(_FakeRunnable(out)).classify("some clause")
    assert [(s.function, s.confidence) for s in scores] == [
        ("Cap On Liability", FunctionConfidence.HIGH),
        ("Indemnification", FunctionConfidence.MEDIUM),
    ]


def test_llm_classifier_drops_low_confidence_off_taxonomy_and_caps_at_three():
    out = _cls([
        ("Cap On Liability", "high"),
        ("Signature", "high"),            # off-taxonomy artifact (not a label or alias) -> dropped
        ("Indemnification", "low"),       # below the medium floor -> dropped
        ("Audit Rights", "medium"),
        ("Governing Law", "high"),
        ("Insurance", "high"),            # 4th kept -> capped out (cap = 3)
    ])
    scores = LlmClauseClassifier(_FakeRunnable(out)).classify("clause")
    assert [s.function for s in scores] == ["Cap On Liability", "Audit Rights", "Governing Law"]


def test_llm_classifier_degrades_to_empty_on_runnable_failure():
    class _Boom:
        def invoke(self, _p):
            raise RuntimeError("model down")

    assert LlmClauseClassifier(_Boom()).classify("clause") == []


def test_legalbert_adapter_wraps_single_label_as_primary_high():
    class _LB:
        def classify(self, texts, **_):
            return ["cap on liability"]

    scores = LegalBertClauseAdapter(_LB()).classify("clause text")
    assert len(scores) == 1
    assert scores[0].function == "Cap On Liability"
    assert scores[0].confidence is FunctionConfidence.HIGH


def test_legalbert_adapter_off_taxonomy_none_is_empty():
    class _LB:
        def classify(self, texts, **_):
            return ["NONE"]

    assert LegalBertClauseAdapter(_LB()).classify("clause") == []


# --- batched classify_spans (option B): sub-batched, INDEX-aligned (not positional), chunk as context ----------


def _sf(idx, scores):
    return SpanFunctions(span_index=idx, functions=[RawScore(function=f, confidence=c) for f, c in scores])


def test_batch_classifier_aligns_by_returned_index_even_out_of_order():
    # LLM returns span 1 BEFORE span 0 -> must align by span_index, not by position
    out = BatchSpanClassification(spans=[
        _sf(1, [("Indemnification", "medium"), ("Governing Law", "low")]),  # low dropped
        _sf(0, [("cap on liability", "high")]),
    ])
    got = LlmBatchClauseClassifier(_FakeRunnable(out)).classify_spans("ctx", ["span A", "span B"])
    assert [[s.function for s in span] for span in got] == [["Cap On Liability"], ["Indemnification"]]


def test_batch_classifier_omitted_span_is_empty():
    out = BatchSpanClassification(spans=[_sf(0, [("Cap On Liability", "high")])])  # spans 1,2 omitted -> []
    got = LlmBatchClauseClassifier(_FakeRunnable(out)).classify_spans("ctx", ["a", "b", "c"])
    assert [len(span) for span in got] == [1, 0, 0]


def test_batch_classifier_out_of_range_index_dropped():
    out = BatchSpanClassification(spans=[_sf(0, [("Cap On Liability", "high")]), _sf(9, [("Insurance", "high")])])
    got = LlmBatchClauseClassifier(_FakeRunnable(out)).classify_spans("ctx", ["a", "b"])  # 9 is out of range
    assert [len(span) for span in got] == [1, 0]


def test_batch_classifier_splits_big_chunk_into_sub_batches_with_offset_alignment():
    from rag_wright.spans import clause_function_classifier as m

    assert m._BATCH_CAP == 10
    calls = []

    class _Multi:  # sub-batches run CONCURRENTLY -> identify the sub-batch by PROMPT CONTENT, not call order
        def invoke(self, prompt):
            calls.append(1)  # list.append is thread-safe under the GIL
            if "s10" in prompt:  # second sub-batch [10,11]: local index 1 -> global 11
                return BatchSpanClassification(spans=[_sf(1, [("Insurance", "high")])])
            return BatchSpanClassification(spans=[_sf(3, [("Cap On Liability", "high")])])  # first: local 3 -> global 3

    got = LlmBatchClauseClassifier(_Multi()).classify_spans("ctx", [f"s{i}" for i in range(12)])
    assert len(calls) == 2                                       # 12 spans, cap 10 -> 2 sub-batch calls
    assert [i for i, span in enumerate(got) if span] == [3, 11]  # offset alignment correct despite concurrency
    assert got[3][0].function == "Cap On Liability"
    assert got[11][0].function == "Insurance"


def test_batch_classifier_degrades_to_per_span_empty_on_failure():
    class _Boom:
        def invoke(self, _p):
            raise RuntimeError("down")

    assert LlmBatchClauseClassifier(_Boom()).classify_spans("ctx", ["a", "b"]) == [[], []]


def test_legalbert_adapter_classify_spans_is_span_level_single_label():
    class _LB:
        def classify(self, texts, **_):
            return ["cap on liability", "NONE", "Indemnification"]

    got = LegalBertClauseAdapter(_LB()).classify_spans("ignored chunk ctx", ["a", "b", "c"])
    assert [[s.function for s in span] for span in got] == [["Cap On Liability"], [], ["Indemnification"]]


def test_categorize_raw_splits_in_taxonomy_from_out_of_taxonomy():
    from rag_wright.spans.clause_function_classifier import categorize_raw

    raws = [
        RawScore(function="Cap On Liability", confidence="high"),
        RawScore(function="OTHER", confidence="high", other_label="Late Delivery Remedies"),  # convention: use other_label
        RawScore(function="Product Returns", confidence="high"),  # real type in function, other_label empty
        RawScore(function="Repurchase of Products", confidence="high", other_label="None"),  # real type in function, junk other_label
        RawScore(function="Indemnification", confidence="low"),   # in-taxonomy but below floor -> not kept
    ]
    in_tax, others = categorize_raw(raws)
    assert [s.function for s in in_tax] == ["Cap On Liability"]
    assert others == ["Late Delivery Remedies", "Product Returns", "Repurchase of Products"]
