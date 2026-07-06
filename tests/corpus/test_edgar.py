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


# --- loose token-overlap candidates (T10 verification prep): evidence, not a bare CIK ---------

from rag_wright.corpus.edgar import LooseCandidate, loose_cik_candidates  # noqa: E402

_TICKERS_LOOSE = [
    {"cik_str": 70858, "ticker": "BAC", "title": "BANK OF AMERICA CORP /DE/"},
    {"cik_str": 51143, "ticker": "IBM", "title": "INTERNATIONAL BUSINESS MACHINES CORP"},
    {"cik_str": 1, "ticker": "FED", "title": "FEDERATED HERMES INC"},  # common-token trap
]


def test_loose_candidate_carries_evidence_not_just_cik():
    cands = loose_cik_candidates("International Business Machines Corporation", _TICKERS_LOOSE)
    assert cands and isinstance(cands[0], LooseCandidate)
    top = cands[0]
    assert top.proposed_cik == "0000051143"
    assert top.registry_name == "INTERNATIONAL BUSINESS MACHINES CORP"  # the evidence
    assert "business" in top.matched_tokens and top.status is MatchStatus.UNVERIFIED


def test_loose_match_finds_bank_of_america_via_overlap():
    cands = loose_cik_candidates("BANK OF AMERICA CORPORATION", _TICKERS_LOOSE)
    assert cands[0].proposed_cik == "0000070858"


def test_loose_common_token_collision_is_visible_in_evidence():
    # "Federated Advisory Services" shares only "federated" with "FEDERATED HERMES INC" -> low score,
    # and the matched_tokens make the weak overlap visible for the human to reject.
    cands = loose_cik_candidates("Federated Advisory Services Company", _TICKERS_LOOSE, min_overlap=0.1)
    fed = [c for c in cands if "federated" in c.matched_tokens]
    assert fed and fed[0].matched_tokens == ["federated"]  # only the common token -> suspicious
