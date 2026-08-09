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
