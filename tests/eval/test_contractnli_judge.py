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


def test_a_failed_call_is_an_error_not_a_neutral_verdict():
    # PS-22: a timeout or a loop must not be scored as the conservative label
    from eval.contractnli_judge import ERROR, _judge_one

    class _Failing:
        def invoke(self, _prompt):
            raise TimeoutError("deadline")

    assert _judge_one(_Failing(), {"premise": "p", "hypothesis": "h"}) == ERROR
