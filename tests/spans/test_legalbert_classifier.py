"""T56 (FR-R, ADR-0025): LegalBERT classifier plumbing (hermetic — fakes, no model download).

The fine-tuned F1 is validated by the training run; here we prove the classify plumbing in isolation: batched
forward, argmax over logits, id -> label mapping via the model config, and empty-input handling.
"""

from __future__ import annotations

import torch

from rag_wright.spans.legalbert_classifier import LegalBertFunctionClassifier


class _FakeEnc(dict):
    def to(self, device):  # noqa: D401 - mimics a transformers BatchEncoding
        return self


class _FakeTokenizer:
    def __call__(self, batch, **_kwargs):
        return _FakeEnc(texts=batch)


class _FakeModel:
    class _Cfg:
        id2label = {0: "Cap On Liability", 1: "Governing Law", 2: "NONE"}

    config = _Cfg()

    def to(self, _device):
        return self

    def eval(self):
        return self

    def __call__(self, **enc):
        rows = []
        for t in enc["texts"]:
            tl = t.lower()
            if "liable" in tl:
                rows.append([2.0, 0.0, 0.0])  # -> Cap On Liability
            elif "govern" in tl:
                rows.append([0.0, 2.0, 0.0])  # -> Governing Law
            else:
                rows.append([0.0, 0.0, 2.0])  # -> NONE
        return type("Out", (), {"logits": torch.tensor(rows)})()


def test_classify_maps_argmax_to_labels_and_batches():
    clf = LegalBertFunctionClassifier(_FakeModel(), _FakeTokenizer(), device="cpu")
    preds = clf.classify(
        ["in no event shall X be liable", "governed by New York law", "boilerplate recital text"],
        batch_size=2,  # 3 items over 2 batches
    )
    assert preds == ["Cap On Liability", "Governing Law", "NONE"]
    assert clf.classify([]) == []
