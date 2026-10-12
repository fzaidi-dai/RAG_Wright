"""EV-3d: scorer 5 (generation / QA) and scorer 7 (ingestion structure and unit labelling), on hand-worked cases."""
from __future__ import annotations

import pytest

from rag_wright.api import (DocumentEvaluation, GoldItem, GoldSet, IngestionEvaluation, LabelEvaluation,
                            score_generation, score_ingestion)

QA = GoldSet(name="qa", version="1", items=[
    GoldItem(id="q1", input="What law governs?", expected=["the laws of the State of New York"]),
    GoldItem(id="q2", input="Who are the parties?", expected=["Acme Corp", "Beta LLC"]),
    GoldItem(id="q3", input="Is there a non-compete?", expected=[]),  # not in the contract: abstain
    GoldItem(id="q4", input="Is there an audit right?", expected=[])])

EV = {"c1": "This Agreement is governed by the laws of the State of New York.",
      "c2": "between ACME Corp. and Gamma Inc", "c3": "Payment is due in 30 days."}


def test_citation_recall_abstention_and_groundedness_are_the_gates():
    preds = {"q1": {"answer": "New York law", "citations": ["c1"], "abstained": False, "evidence": EV},
             "q2": {"answer": "Acme and Gamma", "citations": ["c2"], "abstained": False, "evidence": EV},
             "q3": {"answer": "", "citations": [], "abstained": True, "evidence": EV},
             "q4": {"answer": "Yes", "citations": ["c3", "c9"], "abstained": False, "evidence": EV}}
    r = score_generation(QA, preds, citation_recall=0.7, abstention=0.7, grounded=0.9)
    # q1 covers its answer, q2 covers Acme (punctuation ignored) but not Beta; q4 answered where it should abstain;
    # q4 cites c9, which it was not given
    assert r.gate == {"citation_recall": pytest.approx((1.0 + 0.5) / 2), "abstention": 0.75,
                      "grounded": pytest.approx((1 + 1 + 0.5) / 3)}
    assert r.passed is False
    d = r.diagnostics
    assert d["false_answer_rate"] == 0.5 and d["false_abstain_rate"] == 0.0
    assert d["citations_per_answer"] == pytest.approx(4 / 3) and d["misses"] == ["q2: Beta LLC"]


def test_abstaining_on_an_answerable_question_recalls_nothing():
    preds = {"q1": {"answer_kind": "abstained", "citations": [], "evidence": EV},
             "q2": {"answer_kind": "partial", "citations": ["c2"], "evidence": EV},
             "q3": {"abstained": True, "citations": [], "evidence": EV},
             "q4": {"abstained": True, "citations": [], "evidence": EV}}
    r = score_generation(QA, preds, citation_recall=0.0, abstention=0.0, grounded=0.0)
    assert r.gate["citation_recall"] == pytest.approx((0 + 0.5) / 2) and r.diagnostics["false_abstain_rate"] == 0.5


def test_an_answer_without_a_citation_is_ungrounded():
    preds = {"q1": {"answer": "New York", "citations": [], "abstained": False, "evidence": EV},
             "q2": {"answer": "Acme", "citations": ["c2"], "abstained": False, "evidence": EV},
             "q3": {"abstained": True, "citations": []}, "q4": {"abstained": True, "citations": []}}
    assert score_generation(QA, preds, citation_recall=0.0, abstention=0.0, grounded=0.0).gate["grounded"] == 0.5


def test_a_failed_call_is_an_error_not_an_abstention():
    preds = {"q1": {"answer": "New York law", "citations": ["c1"], "abstained": False, "evidence": EV},
             "q2": {"answer": "Acme", "citations": ["c2"], "abstained": False, "evidence": EV},
             "q4": {"abstained": True, "citations": []}}  # q3 failed
    r = score_generation(QA, preds, citation_recall=0.0, abstention=0.0, grounded=0.0)
    assert r.diagnostics["errors"] == 1 and r.gate["abstention"] == 0.75  # q3 counts wrong, not as a right abstain


def test_judge_verdicts_are_reported_never_gated():
    preds = {q: {"abstained": True, "citations": []} for q in ("q3", "q4")}
    preds.update({"q1": {"answer": "NY", "citations": ["c1"], "evidence": EV},
                  "q2": {"answer": "Acme", "citations": ["c2"], "evidence": EV}})
    r = score_generation(QA, preds, citation_recall=0.0, abstention=0.0, grounded=0.0,
                         judgments={"q1": True, "q2": False})
    assert r.diagnostics["judge_supported"] == 0.5 and "judge_supported" not in r.gate


# --- 7. ingestion --------------------------------------------------------------------------------------------------

def _ev(failures=(), warnings=(), labels=None) -> IngestionEvaluation:
    return IngestionEvaluation(documents=[DocumentEvaluation(doc="a.pdf", spans=10, units=3)],
                               failures=list(failures), warnings=list(warnings), labels=labels)


def test_the_structural_checks_are_the_gate():
    assert score_ingestion(_ev()).passed is True
    failed = score_ingestion(_ev(failures=["a.pdf: coverage = 0.900"], warnings=["a.pdf: a table may continue"]))
    assert failed.passed is False and failed.gate == {"structural_checks": 0.0}
    assert failed.diagnostics["failures"] == ["a.pdf: coverage = 0.900"]
    assert failed.diagnostics["warnings"] == ["a.pdf: a table may continue"]
    assert failed.diagnostics["documents"] == [{"doc": "a.pdf", "spans": 10, "units": 3, "error": None}]


def test_label_accuracy_is_gated_when_a_floor_is_given():
    labels = LabelEvaluation(gold=4, correct=3, accuracy=0.75,
                             per_label={"construction": {"gold": 4, "predicted": 4, "correct": 3, "recall": 0.75,
                                                         "precision": 0.75}},
                             confusions=[{"gold": "construction", "predicted": "", "count": 1}])
    r = score_ingestion(_ev(labels=labels), label_accuracy=0.8)
    assert r.gate == {"structural_checks": 1.0, "label_accuracy": 0.75} and r.passed is False
    assert r.diagnostics["labels"]["confusions"] == [{"gold": "construction", "predicted": "", "count": 1}]
    assert score_ingestion(_ev(labels=labels)).gate == {"structural_checks": 1.0}  # reported only


def test_a_label_floor_without_labels_cannot_pass():
    assert score_ingestion(_ev(), label_accuracy=0.5).passed is False
