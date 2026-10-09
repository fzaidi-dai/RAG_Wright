"""PS-R3: the SetFit ensemble returns each span's averaged function probabilities alongside its top-k tags, through
the adapter, the `clause_function_classification` capability (`with_probabilities`) and the reference span tagger,
so the reference pack's unit representative can vote. Hermetic: fake bodies and heads."""
from __future__ import annotations

import asyncio

import numpy as np

from rag_wright.packs.contracts.spans.clause_function_classifier import SetFitClauseAdapter


class _Body:
    def encode(self, texts, normalize_embeddings=False, batch_size=32):
        return np.zeros((len(texts), 2))


class _Head:
    def __init__(self, rows):
        self.rows = rows

    def predict_proba(self, x):
        return np.asarray(self.rows[: len(x)])


def _adapter(rows_a, rows_b):
    cols = ["Cap On Liability", "Uncapped Liability", "Governing Law"]
    ad = object.__new__(SetFitClauseAdapter)
    ad._np = np
    ad._top_k, ad._threshold, ad._hi, ad._mid, ad._batch = 3, 0.0, 0.6, 0.3, 32
    ad._models = [(_Body(), _Head(rows_a), False, cols), (_Body(), _Head(rows_b), False, cols)]
    ad._labels = sorted(cols)
    ad._lab_idx = {lab: i for i, lab in enumerate(ad._labels)}
    return ad


def test_probabilities_are_the_ensemble_average_and_the_tags_are_unchanged():
    ad = _adapter([[0.2, 0.7, 0.1]], [[0.4, 0.5, 0.1]])
    ((scores, probs),) = ad.classify_spans_with_probabilities("", ["no cap on indemnification"])
    assert [s.function for s in scores] == [s.function for s in ad.classify_spans("", ["no cap on indemnification"])[0]]
    assert probs == {"Uncapped Liability": 0.6, "Cap On Liability": 0.3, "Governing Law": 0.1}


def test_the_reference_tagger_puts_the_probabilities_on_each_span():
    from rag_wright.contracts.ingestion import Span
    from rag_wright.packs.contracts.subgraphs.contract_ingestion_pipeline import function_span_tagger

    ad = _adapter([[0.2, 0.7, 0.1]], [[0.4, 0.5, 0.1]])
    tagger = function_span_tagger(ad, max_concurrency=1)
    span = Span(span_id="c#0", parent_chunk_id="c", span_index=0, start=0, end=5, text="no cap")
    (ts,) = asyncio.run(tagger("no cap", [span]))
    assert ts.primary_tag == "Uncapped Liability" and ts.scores["Uncapped Liability"] == 0.6


def test_the_capability_returns_probabilities_on_request(monkeypatch):
    from rag_wright.packs.contracts.spans import model_capabilities

    ad = _adapter([[0.2, 0.7, 0.1]], [[0.4, 0.5, 0.1]])
    monkeypatch.setattr(model_capabilities, "_setfit_clause_classifier", lambda: ad)
    plain = model_capabilities.clause_function_classification(None, {"chunk_text": "", "span_texts": ["x"]})
    rich = model_capabilities.clause_function_classification(
        None, {"chunk_text": "", "span_texts": ["x"], "with_probabilities": True})
    assert isinstance(plain[0], list)  # the default output is unchanged
    assert rich[0][1]["Uncapped Liability"] == 0.6 and rich[0][0][0].function == "Uncapped Liability"
