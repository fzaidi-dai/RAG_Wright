"""KG-5a: jurisdiction canonicalization (deterministic; over the real ACORD surface forms)."""

from __future__ import annotations

import pytest

from rag_wright.packs.contracts.schemas.jurisdiction import canonicalize_jurisdiction as canon


@pytest.mark.parametrize("surface", [
    "England", "England and Wales", "English law", "English courts", "United Kingdom",
])
def test_england_group_canonicalizes_together(surface):
    assert canon(surface) == "england"


@pytest.mark.parametrize("surface", [
    "New York", "New York State", "State of New York", "State of New York, USA",
    "State of New York, U.S.A.", "State of New York, United States", "the State of New York",
])
def test_new_york_group_canonicalizes_together(surface):
    assert canon(surface) == "new_york"


@pytest.mark.parametrize("surface", [
    "California", "California, USA", "State of California", "State of California, USA", "the State of California",
])
def test_california_group(surface):
    assert canon(surface) == "california"


def test_state_prefix_and_commonwealth_variants():
    assert canon("State of Delaware") == canon("Delaware") == "delaware"
    assert canon("Commonwealth of Pennsylvania") == "pennsylvania"
    assert canon("the Commonwealth of Virginia") == "virginia"
    assert canon("the State of Indiana, U.S.A.") == "indiana"
    assert canon("State of Illinois (U.S.A.)") == "illinois"


@pytest.mark.parametrize("surface", ["PRC", "PRC Law", "People's Republic of China", "the People's Republic of China"])
def test_china_group(surface):
    assert canon(surface) == "china"


def test_other_countries():
    assert canon("State of Israel") == "israel"
    assert canon("Italian Law") == "italy"
    assert canon("Belgium") == "belgium"
    assert canon("United States") == canon("United States and its territories") == "united_states"


@pytest.mark.parametrize("surface", [
    "Applicable Law", "applicable law", "Mandatory Applicable Law", "Governing Law",
    "Not specified in the text", "None specified", "unspecified", "Other", "worldwide",
    "any jurisdiction", "United States Bankruptcy Code", "Best's rating",
    "Illinois or New York",  # compound -> unresolved
    "internal laws of the [***] applicable to agreements made and to be performed entirely in such state",
])
def test_non_jurisdictions_resolve_to_none(surface):
    assert canon(surface) is None


def test_unknown_returns_none_not_crash():
    assert canon("") is None
    assert canon("Kingdom of Wakanda") is None


def test_containment_fallback_state_over_federal():
    assert canon("state of New York and of the United States of America") == "new_york"
    assert canon("the State of Illinois and the United States of America") == "illinois"
    assert canon("United States and the State of Florida") == "florida"
    assert canon("Miaoli District Court of Taiwan") == "taiwan"


def test_containment_keeps_ambiguous_compounds_none():
    assert canon("Illinois or New York") is None  # two states, genuinely ambiguous
