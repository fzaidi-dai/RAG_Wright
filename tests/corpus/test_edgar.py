"""Tests for EDGAR entity acquisition logic (T7).

Two guarantees: `normalize_cik` is the single canonical-CIK->EntityId normalization (zero-pad-to-10
then strict validate, tied to T1) that T8's registry loader reuses; and name->CIK proposals are
structurally UNVERIFIED (an explicit status + match coverage), because linking a party to a CIK is
the entity-resolution problem (FR-C.7) and only human verification (T10) yields ground truth.
"""

import pytest
from pydantic import ValidationError

from rag_wright.contracts.identifiers import EntityId
from rag_wright.corpus.edgar import (
    MatchStatus,
    normalize_cik,
    propose_matches,
)


# --- normalize_cik: the canonical CIK -> EntityId point (T1/T8 tie) --------------------------


def test_normalize_cik_zero_pads_and_returns_canonical_entity_id():
    assert normalize_cik(320193) == EntityId.of("0000320193")
    assert normalize_cik("320193") == EntityId.of("0000320193")
    assert normalize_cik("0000320193") == EntityId.of("0000320193")
    assert normalize_cik("CIK0000320193") == EntityId.of("0000320193")


def test_normalize_cik_result_is_a_strict_entity_id():
    eid = normalize_cik(320193)
    assert isinstance(eid, EntityId)
    assert eid.value == "0000320193"


@pytest.mark.parametrize("bad", [10**11, "abc", "12A45", "", True, 3.5])
def test_normalize_cik_rejects_invalid(bad):
    with pytest.raises((ValueError, ValidationError)):
        normalize_cik(bad)


# --- propose_matches: mechanical, UNVERIFIED, with match coverage ---------------------------


_TICKERS = [
    {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corporation"},
]


def test_proposal_resolves_known_party_and_marks_it_unverified():
    coverage = propose_matches(["Apple Inc."], _TICKERS)
    assert len(coverage.resolved) == 1
    proposal = coverage.resolved[0]
    assert proposal.proposed_cik == "0000320193"
    assert proposal.status is MatchStatus.UNVERIFIED  # never ground truth from T7
    assert coverage.unresolved == []


def test_proposal_matching_is_case_and_punctuation_insensitive():
    coverage = propose_matches(["APPLE INC"], _TICKERS)
    assert coverage.resolved and coverage.resolved[0].proposed_cik == "0000320193"


def test_match_coverage_records_unresolved_parties():
    coverage = propose_matches(["Apple Inc.", "Nonpublic Family LLC"], _TICKERS)
    assert [p.party_name for p in coverage.resolved] == ["Apple Inc."]
    assert coverage.unresolved == ["Nonpublic Family LLC"]


def test_match_coverage_partitions_all_input_parties():
    parties = ["Apple Inc.", "Microsoft Corporation", "Unknown Co"]
    coverage = propose_matches(parties, _TICKERS)
    covered = {p.party_name for p in coverage.resolved} | set(coverage.unresolved)
    assert covered == set(parties)


def test_t7_never_emits_a_verified_status():
    coverage = propose_matches(["Apple Inc.", "Microsoft Corporation"], _TICKERS)
    assert all(p.status is MatchStatus.UNVERIFIED for p in coverage.resolved)
