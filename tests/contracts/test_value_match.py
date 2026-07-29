"""KG-5a: subsumption + canonicalized value matching (pure)."""

from __future__ import annotations

from rag_wright.contracts.value_match import constraint_match_count, satisfied_values, value_satisfies


def test_subsumption_specific_satisfies_broader():
    assert value_satisfies("covered_parties", "affiliates", "licensor_affiliates")
    assert value_satisfies("covered_parties", "affiliates", "licensee_affiliates")
    assert value_satisfies("restriction_scope", "geographic", "geographic_and_activity")
    assert value_satisfies("restriction_scope", "activity", "geographic_and_activity")
    assert value_satisfies("mfn_scope", "price", "price_and_terms")
    assert value_satisfies("mfn_scope", "terms", "price_and_terms")


def test_subsumption_is_one_directional():
    # a broader clause value does NOT satisfy a query for the specific one
    assert not value_satisfies("covered_parties", "licensor_affiliates", "affiliates")


def test_jurisdiction_canonicalized_match():
    assert value_satisfies("jurisdiction", "england", "England and Wales")
    assert value_satisfies("jurisdiction", "England", "English law")
    assert not value_satisfies("jurisdiction", "england", "New York")
    assert not value_satisfies("jurisdiction", "worldwide", "England")  # junk query -> no match


def test_exact_for_flat_dims():
    assert value_satisfies("mutuality", "mutual", "mutual")
    assert not value_satisfies("mutuality", "mutual", "unilateral")


def test_satisfied_values_rollup():
    assert satisfied_values("covered_parties", "licensor_affiliates") == {"licensor_affiliates", "affiliates"}
    assert satisfied_values("mutuality", "mutual") == {"mutual"}  # no rollup


def test_constraint_match_count_uses_subsumption_and_canon():
    qc = {("covered_parties", "affiliates"), ("jurisdiction", "england")}
    props = {("covered_parties", "licensor_affiliates"), ("jurisdiction", "England and Wales")}
    assert constraint_match_count(qc, props) == 2  # both match via subsumption + canon
    assert constraint_match_count({("cap_basis", "fixed_fee")}, props) == 0
