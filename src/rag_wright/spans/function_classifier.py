"""T56 (FR-R, ADR-0025): local single-label clause-function classifier.

A linear head over frozen dense embeddings of an operative span, predicting one of the 41 CUAD clause types
(`ClauseCategory`) or NONE. Trained on CUAD gold spans (segmented to operative-span granularity, T55), applied
at inference to ACORD operative spans. Inference is a matmul -- local, deterministic, no LLM call, categorically
more scalable than an LLM routing call per clause. If the per-type F1 lags, the plan escalates the feature side
to a fine-tuned encoder (LegalBERT); the head/contract here stay the same.

The embedder is an injected seam (`DenseEmbedder`), so training and inference share one representation and the
hermetic tests run with a stub -- no model load.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import joblib
from sklearn.linear_model import LogisticRegression

NONE_LABEL = "NONE"  # the span is none of the 41 CUAD clause types (boilerplate / recital / definition)


@runtime_checkable
class DenseEmbedder(Protocol):
    """The dense-embedding seam (the T5 `Embedder`'s dense leg): span text -> a fixed-length vector."""

    def encode_dense(self, text: str) -> list[float]: ...


class FunctionClassifier:
    """A fitted single-label function classifier over span dense embeddings (predicts a CUAD type or NONE)."""

    def __init__(self, model: LogisticRegression) -> None:
        self._model = model

    @classmethod
    def train_on_features(
        cls, features: list[list[float]], labels: list[str], **model_kwargs: Any
    ) -> "FunctionClassifier":
        """Fit the linear head on precomputed dense features (the batch path -- callers batch-embed first).
        `class_weight='balanced'` offsets the heavy NONE class; dimension-agnostic (fits the feature width)."""
        if len(features) != len(labels):
            raise ValueError("features and labels must be the same length")
        if not features:
            raise ValueError("need at least one training example")
        params: dict[str, Any] = {"max_iter": 2000, "class_weight": "balanced"}
        params.update(model_kwargs)
        return cls(LogisticRegression(**params).fit(features, labels))

    @classmethod
    def train(
        cls, texts: list[str], labels: list[str], *, embedder: DenseEmbedder, **model_kwargs: Any
    ) -> "FunctionClassifier":
        """Convenience: embed `texts` one-by-one via the seam, then fit (used by tests / small sets)."""
        if len(texts) != len(labels):
            raise ValueError("texts and labels must be the same length")
        return cls.train_on_features([embedder.encode_dense(t) for t in texts], labels, **model_kwargs)

    def classify_features(self, features: list[list[float]]) -> list[str]:
        """One label per precomputed feature vector (a CUAD type or NONE). Empty input -> empty output."""
        if not features:
            return []
        return [str(y) for y in self._model.predict(features)]

    def classify(self, texts: list[str], *, embedder: DenseEmbedder) -> list[str]:
        """Convenience: embed `texts` via the seam, then classify. Empty input -> empty output."""
        if not texts:
            return []
        return self.classify_features([embedder.encode_dense(t) for t in texts])

    def save(self, path: Path) -> None:
        joblib.dump(self._model, path)

    @classmethod
    def load(cls, path: Path) -> "FunctionClassifier":
        return cls(joblib.load(path))
