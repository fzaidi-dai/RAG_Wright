"""T56 (FR-R, ADR-0025): LegalBERT fine-tuned function classifier (the upgrade over the linear head).

A fine-tuned `nlpaueb/legal-bert-base-uncased` sequence classifier: an operative span's text -> one of the 41
CUAD clause types or NONE. Self-contained (its own tokenizer + encoder), sub-second local inference (MPS/CPU),
no LLM call. Chosen to make the classifier a controlled, high-accuracy variable (it captures fine legal
distinctions the frozen-embedding linear head could not, e.g. Cap-on-Liability vs Uncapped-Liability), so a
later downstream recall shortfall is attributable to the property/rerank stages, not the classifier.

The tokenizer + model are injected, so the classify plumbing (batching, argmax, id->label) is unit-tested with
fakes and no model download; training/loading use the real `transformers` classes (see
`scripts/train_legalbert_function.py`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import torch


class LegalBertFunctionClassifier:
    """A fine-tuned LegalBERT sequence classifier over operative spans (predicts a CUAD type or NONE)."""

    def __init__(self, model: Any, tokenizer: Any, *, device: str = "cpu", max_length: int = 256) -> None:
        self._model = model.to(device).eval()
        self._tokenizer = tokenizer
        self._device = device
        self._max_length = max_length

    @classmethod
    def load(cls, path: Path, *, device: str = "cpu") -> "LegalBertFunctionClassifier":
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(str(path))
        model = AutoModelForSequenceClassification.from_pretrained(str(path))
        return cls(model, tokenizer, device=device)

    @torch.no_grad()
    def classify(self, texts: list[str], *, batch_size: int = 32) -> list[str]:
        """One label per span (a CUAD type or NONE). Empty input -> empty output. Batched forward on the device."""
        if not texts:
            return []
        id2label = self._model.config.id2label
        out: list[str] = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            enc = self._tokenizer(
                batch, truncation=True, max_length=self._max_length, padding=True, return_tensors="pt"
            ).to(self._device)
            logits = self._model(**enc).logits
            out.extend(str(id2label[int(i)]) for i in logits.argmax(dim=-1).tolist())
        return out

    @torch.no_grad()
    def classify_topk(self, texts: list[str], *, k: int = 2, batch_size: int = 32) -> list[list[str]]:
        """The top-`k` labels per span (highest logit first). Feeds the hybrid classifier's routing decision
        (route to the LLM when the top-2 are confusable siblings). Empty input -> empty output."""
        if not texts:
            return []
        id2label = self._model.config.id2label
        out: list[list[str]] = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            enc = self._tokenizer(
                batch, truncation=True, max_length=self._max_length, padding=True, return_tensors="pt"
            ).to(self._device)
            logits = self._model(**enc).logits
            topk = logits.topk(min(k, logits.shape[-1]), dim=-1).indices.tolist()
            out.extend([str(id2label[int(i)]) for i in row] for row in topk)
        return out


def register_clause_function_classification(registry) -> None:
    """CAP-REG-2: register `clause_function_classification` (model; fine-tuned LegalBERT classifier)."""
    from rag_wright.contracts.function import FunctionClassification

    registry.register(
        "clause_function_classification",
        contract=FunctionClassification,
        kind="model",
        display_name="Clause function classification (LegalBERT)",
    )
