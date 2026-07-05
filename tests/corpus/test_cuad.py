"""Tests for CUAD parsing + scanned detection (T7). Pure logic, no data files or network."""

import pytest

from rag_wright.corpus import cuad
from rag_wright.corpus.cuad import is_scanned_by_chars, party_entities


def test_party_entities_keeps_companies_and_drops_role_labels():
    # The real CUAD shape: company names mixed with defined-term role labels.
    raw = (
        "['BIRCH FIRST GLOBAL INVESTMENTS INC.', 'MA', 'Marketing Affiliate', "
        "'MOUNT KNOWLEDGE HOLDINGS INC.', 'Company']"
    )
    assert party_entities(raw) == [
        "BIRCH FIRST GLOBAL INVESTMENTS INC.",
        "MOUNT KNOWLEDGE HOLDINGS INC.",
    ]


def test_party_entities_dedupes_case_insensitively():
    raw = "['Acme Corp', 'ACME CORP', 'Beta LLC']"
    assert party_entities(raw) == ["Acme Corp", "Beta LLC"]


@pytest.mark.parametrize("raw", ["", "not a list", "[]", "['Buyer', 'Seller']"])
def test_party_entities_handles_empty_and_role_only(raw):
    # No corporate-suffix names -> no phantom entities.
    assert party_entities(raw) == []


def test_scanned_threshold_is_low_count_not_strict_zero():
    # A stray character on a scanned page must still count as scanned.
    assert is_scanned_by_chars(0)
    assert is_scanned_by_chars(5)  # a few stray chars -> still scanned
    assert is_scanned_by_chars(cuad.SCANNED_CHAR_THRESHOLD - 1)
    assert not is_scanned_by_chars(cuad.SCANNED_CHAR_THRESHOLD)
    assert not is_scanned_by_chars(5000)  # a digital contract


def test_is_scanned_pdf_composes_char_count(monkeypatch, tmp_path):
    monkeypatch.setattr(cuad, "pdf_text_chars", lambda p, **k: 10)
    assert cuad.is_scanned_pdf(tmp_path / "x.pdf")
    monkeypatch.setattr(cuad, "pdf_text_chars", lambda p, **k: 4000)
    assert not cuad.is_scanned_pdf(tmp_path / "x.pdf")
