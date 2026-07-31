"""HYG-1: the ONE canonical filename/title -> source_doc_id slug.

The `_`-vs-`-` divergence (HYG-1 root cause) was five roll-your-own slugifiers disagreeing on the replacement
character. `canonical_source_doc_id` is the single derivation every ingestion path now calls, so the same
document gets the same `source_doc_id` everywhere. Canonical form: any run of characters outside the
delimiter-safe set `[A-Za-z0-9._-]` collapses to a single `_`, leading/trailing `_` stripped.
"""

from __future__ import annotations

import pytest

from rag_wright.contracts.identifiers import ChunkId, canonical_source_doc_id


def test_space_becomes_underscore_not_hyphen():
    # the exact bug: a title space -> '_' (the Contract/Clause form), never '-' (the Entity form)
    assert canonical_source_doc_id("SPONSORSHIP AGREEMENT") == "SPONSORSHIP_AGREEMENT"


def test_preserves_existing_safe_delimiters():
    # the '-' and '.' inside EX-4 / EX-10.1 / 10-Q are already safe and must NOT change
    assert canonical_source_doc_id("XYBERNAUTCORP_07_12_2002-EX-4-SPONSORSHIP AGREEMENT") == (
        "XYBERNAUTCORP_07_12_2002-EX-4-SPONSORSHIP_AGREEMENT"
    )


def test_collapses_runs_and_strips_edges():
    assert canonical_source_doc_id("  Acme  &  Beta, Inc.  ") == "Acme_Beta_Inc."
    assert canonical_source_doc_id("&&lead&&") == "lead"


def test_idempotent_on_canonical_input():
    once = canonical_source_doc_id("Intellectual Property & Licensing Agreement")
    assert canonical_source_doc_id(once) == once  # applying it twice changes nothing


def test_empty_or_all_unsafe_raises():
    for bad in ("", "   ", "&&&", None):
        with pytest.raises(ValueError):
            canonical_source_doc_id(bad)  # surface the defect loudly, never a silent collision


def test_output_is_a_valid_source_doc_id():
    # the canonical slug always satisfies the ChunkId source_doc_id contract
    sid = canonical_source_doc_id("Some Messy Title, LLC (2020)")
    ChunkId(source_doc_id=sid, chunk_index=0, content_hash="a" * 64)  # does not raise


def test_regression_the_two_paths_now_agree():
    # the same raw CUAD title must yield ONE id, whatever path derives it (the bug was two forms)
    raw = "ZEBRATECHNOLOGIESCORP_04_16_2014-EX-10.1-INTELLECTUAL PROPERTY AGREEMENT"
    assert canonical_source_doc_id(raw) == "ZEBRATECHNOLOGIESCORP_04_16_2014-EX-10.1-INTELLECTUAL_PROPERTY_AGREEMENT"
