"""Tests for entity canonicalization (T23b method, used first for the T10 eval set).

Pins the normalize/reject/cluster rules that harden into T23b: legal-suffix + whitespace variants of
one entity normalize to a shared key and cluster into one node (the Bank-of-America fix), while
template placeholders, role artifacts, and bare over-broad tokens are rejected (never enter the
graph).
"""

import pytest

from rag_wright.corpus.canonicalize import (
    cluster_entities,
    is_entity,
    normalize_entity_name,
)


@pytest.mark.parametrize(
    "variant",
    ["Bank of America", "Bank of America, N.A.", "Bank of America, N. A", "Bank of America, N.A"],
)
def test_legal_suffix_and_whitespace_variants_share_one_key(variant):
    assert normalize_entity_name(variant) == "bank of america"


def test_stacked_suffixes_are_stripped():
    assert normalize_entity_name("Beijing SINA Internet Service Co., Ltd") == "beijing sina internet service"
    assert normalize_entity_name("Cisco Systems, Inc.") == "cisco systems"


def test_does_not_over_strip_names_ending_in_suffix_letters():
    assert normalize_entity_name("Costco") == "costco"  # not "cost"


@pytest.mark.parametrize(
    "noise",
    ["Bank", "<<enter Company Name>>", '(collectively the "Company").', "Company", "Group", "Holdings", "",
     "the Company and together with Buyer the Buyer Entities", "together with Seller"],
)
def test_rejects_non_entities(noise):
    assert not is_entity(noise)


@pytest.mark.parametrize(
    "real",
    ["Bank of America, N.A.", "ScanSource, Inc.", "American International Group, Inc."],
)
def test_keeps_real_entities(real):
    assert is_entity(real)


def test_cluster_merges_variants_into_one_entity():
    names = [
        "Bank of America",
        "Bank of America, N.A.",
        "Bank of America, N. A",
        "ScanSource, Inc.",
        "ScanSource Inc",
        "Bank",  # noise -> rejected
    ]
    clusters = cluster_entities(names)
    by_key = {c.key: c for c in clusters}
    assert set(by_key) == {"bank of america", "scansource"}  # 2 entities, noise dropped
    assert len(by_key["bank of america"].variants) == 3
    assert by_key["bank of america"].representative == "Bank of America, N.A."  # longest form


# --- T10-surfaced hardening: possessives, bare generics, alias clauses -----------------------


def test_possessive_apostrophe_variants_merge():
    assert normalize_entity_name("Stremick's Heritage Foods, LLC") == normalize_entity_name(
        "Stremicks Heritage Foods, LLC"
    )
    assert normalize_entity_name("Stremick's Heritage Foods, LLC") == "stremicks heritage foods"


@pytest.mark.parametrize("bare", ["Services", "Solutions", "Systems", "International", "Technologies"])
def test_rejects_bare_generic_token_fragments(bare):
    assert not is_entity(bare)


def test_keeps_generic_token_inside_a_real_name():
    assert is_entity("VerticalNet Solutions")
    assert is_entity("Cisco Systems, Inc.")


def test_alias_clause_recovers_name_before_marker():
    assert normalize_entity_name("Acme Widgets Inc. d/b/a AcmeBrand") == "acme widgets"
    assert is_entity("Acme Widgets Inc. d/b/a AcmeBrand")


def test_alias_only_mention_is_rejected():
    # a mention that is only an alias clause has no name before the marker -> empty -> rejected
    assert not is_entity("formerly known as Tradeum, Inc. which d/b/a VerticalNet Solutions")
    assert normalize_entity_name("formerly known as Tradeum, Inc.") == ""
