"""CU-D1: the CUAD-highlight eval metric functions (SQuAD-style token PRF + normalized coverage). Hermetic."""

from __future__ import annotations

from eval.cuad_highlight import covers, token_prf


def test_exact_match_is_perfect():
    p, r, f = token_prf("Delaware law", "Delaware law")
    assert (round(p, 3), round(r, 3), round(f, 3)) == (1.0, 1.0, 1.0)


def test_whole_clause_covers_gold_high_recall_low_precision():
    # highlighting returns the whole clause; the gold is a short sub-answer -> recall 1, precision < 1
    pred = "This Agreement shall be governed by the laws of the State of Delaware without regard to conflicts."
    p, r, f = token_prf(pred, "State of Delaware")
    assert r == 1.0  # all gold tokens surfaced
    assert p < 0.5  # whole clause is much longer -> low precision (expected, not an error)


def test_no_overlap_is_zero():
    assert token_prf("insurance coverage terms", "governing law state") == (0.0, 0.0, 0.0)


def test_empty_pred_or_gold_is_zero():
    assert token_prf("", "anything") == (0.0, 0.0, 0.0)
    assert token_prf("anything", "") == (0.0, 0.0, 0.0)


def test_normalization_ignores_case_punctuation_whitespace():
    p, r, f = token_prf("GOVERNED   by\nDELAWARE, law.", "governed by delaware law")
    assert round(r, 3) == 1.0 and round(f, 3) == 1.0


def test_covers_true_when_gold_is_a_normalized_substring():
    pred = "10. Miscellaneous.  This Agreement is governed by\nCalifornia law."
    assert covers("governed by California law", pred) is True
    assert covers("California", pred) is True


def test_covers_false_when_absent_or_empty():
    assert covers("Delaware", "governed by California law") is False
    assert covers("", "anything") is False
