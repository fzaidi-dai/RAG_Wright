"""CU-D2: the NL->type eval metric predicates. Hermetic."""

from __future__ import annotations

from eval.nl_to_type import exact_hit, multi_hit, type_hit


def test_type_hit_any_match():
    assert type_hit(["Governing Law"], ["Governing Law"]) is True
    assert type_hit(["Governing Law", "Insurance"], ["Insurance"]) is True  # gold among several predicted
    assert type_hit(["Insurance"], ["Governing Law"]) is False
    assert type_hit([], ["Governing Law"]) is False


def test_exact_hit():
    assert exact_hit(["Governing Law"], ["Governing Law"]) is True
    assert exact_hit(["Governing Law", "Insurance"], ["Governing Law"]) is False  # extra predicted -> not exact


def test_multi_hit_order_insensitive():
    assert multi_hit(["Insurance", "Governing Law"], ["Governing Law", "Insurance"]) is True
    assert multi_hit(["Governing Law", "Insurance", "Parties"], ["Governing Law", "Insurance"]) is True  # superset ok
    assert multi_hit(["Governing Law"], ["Governing Law", "Insurance"]) is False  # one missing


def test_empty_gold_is_not_a_type_hit():
    assert type_hit(["Governing Law"], []) is False  # out-of-taxonomy gold has no label to match
