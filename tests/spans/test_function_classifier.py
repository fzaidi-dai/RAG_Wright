"""T56 (FR-R, ADR-0025): tests for the function classifier + CUAD operative-span labeling.

Hermetic: a stub dense embedder (keyword -> vector) drives the classifier so training/inference plumbing is
tested without a model load; CUAD labeling is exercised on a synthetic contract so the overlap rule and the
type parsing are checked deterministically. The real per-type F1 (BGE-M3 over CUAD) is an opt-in training run.
"""

from __future__ import annotations

import json

from rag_wright.packs.contracts.spans.cuad_labels import (
    CuadAnswer,
    CuadContract,
    label_operative_spans,
    parse_cuad,
)
from rag_wright.packs.contracts.spans.function_classifier import NONE_LABEL, FunctionClassifier


class _StubEmbedder:
    """Deterministic keyword -> vector, so the classes are linearly separable for the test."""

    def encode_dense(self, text: str) -> list[float]:
        t = text.lower()
        return [
            1.0 if "liable" in t or "liability" in t else 0.0,
            1.0 if "govern" in t else 0.0,
            1.0 if "indemnif" in t else 0.0,
            1.0,  # bias so the all-zero (NONE) case is still separable
        ]


# --- classifier ---------------------------------------------------------------------------------


def _trained() -> FunctionClassifier:
    texts = [
        "shall not be liable for damages", "total liability is capped at fees",
        "governed by New York law", "governing law shall be Delaware",
        "party shall indemnify and hold harmless", "the indemnifying party defends",
        "recitals and definitions appear here", "this section is boilerplate",
    ]
    labels = [
        "Cap On Liability", "Cap On Liability", "Governing Law", "Governing Law",
        "Indemnification", "Indemnification", NONE_LABEL, NONE_LABEL,
    ]
    return FunctionClassifier.train(texts, labels, embedder=_StubEmbedder())


def test_classifier_predicts_single_label_and_none():
    clf = _trained()
    preds = clf.classify(
        ["in no event shall Supplier be liable", "this is governed by English law",
         "Supplier will indemnify Buyer", "unrelated background text"],
        embedder=_StubEmbedder(),
    )
    assert preds == ["Cap On Liability", "Governing Law", "Indemnification", NONE_LABEL]


def test_classifier_empty_input_and_roundtrip(tmp_path):
    clf = _trained()
    assert clf.classify([], embedder=_StubEmbedder()) == []
    path = tmp_path / "clf.joblib"
    clf.save(path)
    reloaded = FunctionClassifier.load(path)
    assert reloaded.classify(["liability capped"], embedder=_StubEmbedder()) == ["Cap On Liability"]


# --- CUAD labeling ------------------------------------------------------------------------------


def test_label_operative_spans_by_answer_overlap():
    context = "In no event shall Supplier be liable for damages. This Agreement is governed by New York law."
    contract = CuadContract(
        contract_id="c1",
        context=context,
        answers=[
            CuadAnswer(clause_type="Cap On Liability", start=0,
                       text="In no event shall Supplier be liable for damages."),
            CuadAnswer(clause_type="Governing Law", start=context.index("This"),
                       text="This Agreement is governed by New York law."),
        ],
    )
    labeled = label_operative_spans(contract)
    assert "".join(s.text for s in labeled)  # spans present
    by_label = {ls.label for ls in labeled}
    assert {"Cap On Liability", "Governing Law"} <= by_label  # each operative span got its clause type


def test_label_none_when_no_answer_overlaps():
    contract = CuadContract(contract_id="c2", context="This clause has no labeled answer span at all here.",
                            answers=[])
    labeled = label_operative_spans(contract)
    assert labeled and all(ls.label == NONE_LABEL for ls in labeled)


def test_parse_cuad_extracts_type_and_answers(tmp_path):
    data = {"version": "1", "data": [{"title": "c1", "paragraphs": [{"context": "some contract text here",
        "qas": [
            {"question": 'Highlight the parts (if any) of this contract related to "Cap On Liability" that ...',
             "answers": [{"text": "liable for damages", "answer_start": 5}]},
            {"question": 'Highlight the parts related to "Insurance" ...', "answers": []},  # unlabeled -> skipped
        ]}]}]}
    p = tmp_path / "cuad.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    contracts = list(parse_cuad(p))
    assert len(contracts) == 1
    c = contracts[0]
    assert c.contract_id == "c1" and len(c.answers) == 1
    assert c.answers[0].clause_type == "Cap On Liability" and c.answers[0].text == "liable for damages"
