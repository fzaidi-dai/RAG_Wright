"""INGEST-LLM-CLASSIFIER (ADR-0048): the clause-level function classifier seam. Hermetic -- the LLM structured
runnable is injected, no real model. Covers: canonicalize + confidence-floor + cap post-processing, the LegalBERT
back-compat adapter, and graceful degrade-to-empty on a runnable failure."""

from __future__ import annotations

from rag_wright.contracts.function import FunctionConfidence
from rag_wright.spans.clause_function_classifier import (
    ClauseFunctionClassification,
    LegalBertClauseAdapter,
    LlmClauseClassifier,
    RawScore,
)


class _FakeRunnable:
    def __init__(self, out):
        self._out = out

    def invoke(self, _prompt):
        return self._out


def _cls(scores):
    return ClauseFunctionClassification(functions=[RawScore(function=f, confidence=c) for f, c in scores])


def test_llm_classifier_keeps_ranked_canonicalized_scores_above_floor():
    out = _cls([("cap on liability", "high"), ("Indemnification", "medium")])
    scores = LlmClauseClassifier(_FakeRunnable(out)).classify("some clause")
    assert [(s.function, s.confidence) for s in scores] == [
        ("Cap On Liability", FunctionConfidence.HIGH),   # canonicalized casing, order preserved (primary first)
        ("Indemnification", FunctionConfidence.MEDIUM),
    ]


def test_llm_classifier_drops_low_confidence_off_taxonomy_and_caps_at_three():
    out = _cls([
        ("Cap On Liability", "high"),
        ("Force Majeure", "high"),        # off-taxonomy -> dropped
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
            return ["cap on liability"]  # span-level single label, CUAD casing

    scores = LegalBertClauseAdapter(_LB()).classify("clause text")
    assert len(scores) == 1
    assert scores[0].function == "Cap On Liability"
    assert scores[0].confidence is FunctionConfidence.HIGH


def test_legalbert_adapter_off_taxonomy_none_is_empty():
    class _LB:
        def classify(self, texts, **_):
            return ["NONE"]

    assert LegalBertClauseAdapter(_LB()).classify("clause") == []


# --- batched classify_spans (option B): one LLM call per chunk, per-span aligned output, chunk as context ------

from rag_wright.spans.clause_function_classifier import BatchSpanClassification, LlmBatchClauseClassifier  # noqa: E402


def _batch(per_span):
    return BatchSpanClassification(
        spans=[ClauseFunctionClassification(functions=[RawScore(function=f, confidence=c) for f, c in s])
               for s in per_span])


def test_batch_classifier_aligns_scores_to_each_span_in_order():
    out = _batch([
        [("cap on liability", "high")],
        [("Indemnification", "medium"), ("Governing Law", "low")],  # low dropped
    ])
    got = LlmBatchClauseClassifier(_FakeRunnable(out)).classify_spans("chunk context", ["span A", "span B"])
    assert [[s.function for s in span] for span in got] == [["Cap On Liability"], ["Indemnification"]]


def test_batch_classifier_pads_missing_and_truncates_extra_span_results():
    # LLM returned only 1 classification for 3 spans -> spans 2,3 get [] (aligned to input length)
    out = _batch([[("Cap On Liability", "high")]])
    got = LlmBatchClauseClassifier(_FakeRunnable(out)).classify_spans("ctx", ["a", "b", "c"])
    assert [len(span) for span in got] == [1, 0, 0]


def test_batch_classifier_empty_spans_is_empty():
    assert LlmBatchClauseClassifier(_FakeRunnable(_batch([]))).classify_spans("ctx", []) == []


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
