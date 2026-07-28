"""GP-1B.4: the party-extraction scorer for the docling-graph model A/B. CUAD 'Parties' gold mixes real
org/person names with bare role labels (Distributor, Company, the Customer), so `score_parties` filters gold
to name-like entries and matches on normalized (legal-suffix-stripped) containment. Hermetic."""

from __future__ import annotations

from scripts.dg_model_ab import _is_name, score_parties


def test_role_labels_are_not_names():
    assert not _is_name("Distributor")
    assert not _is_name("the Customer")
    assert not _is_name("Company")
    assert _is_name("Electric City Corp.")  # corporate suffix
    assert _is_name("Google Inc")
    assert _is_name("Tom Watson")  # two-token person name


def test_full_recall_precision_with_suffix_variance():
    # gold has a role (Company, filtered) + two names; extracted matches both names despite suffix variance
    r, p = score_parties({"Acme Corp", "Beta Incorporated"},
                         {"Acme Corporation", "Beta Inc.", "Company"})
    assert r == 1.0 and p == 1.0


def test_partial_recall():
    r, p = score_parties({"Acme Corp"}, {"Acme Corporation", "Beta Inc"})
    assert r == 0.5 and p == 1.0  # found 1 of 2 gold names, no junk


def test_precision_penalizes_junk():
    r, p = score_parties({"Acme Corp", "Random Product Name Systems"}, {"Acme Corporation"})
    assert r == 1.0 and p == 0.5  # gold found, but half the extractions are not gold


def test_contract_with_no_name_gold_returns_none():
    assert score_parties({"Acme Corp"}, {"Distributor", "Company"}) is None  # no scorable gold
