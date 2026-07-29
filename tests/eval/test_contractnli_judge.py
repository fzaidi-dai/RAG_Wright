"""C-6: the judgment-node label normalizer (conservative default to neutral)."""

from __future__ import annotations

from eval.contractnli_judge import _norm


def test_norm_canonicalizes_known_labels():
    assert _norm("Entailment") == "entailment"
    assert _norm(" CONTRADICTION ") == "contradiction"
    assert _norm("neutral") == "neutral"


def test_norm_maps_unknown_or_empty_to_neutral():
    assert _norm("maybe") == "neutral"
    assert _norm("") == "neutral"
    assert _norm(None) == "neutral"  # type: ignore[arg-type]
