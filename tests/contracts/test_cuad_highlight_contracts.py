"""CU-A1: contracts for the CUAD clause-highlighting pipeline (ADR-0029)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from rag_wright.contracts.chunk import BGE_M3_DENSE_DIM
from rag_wright.packs.contracts.schemas.contract_meta import ContractRecord
from rag_wright.packs.contracts.schemas.highlight import HighlightResult, HighlightSpan
from rag_wright.packs.contracts.schemas.query_intent import QueryIntent
from rag_wright.contracts.span import SpanRecord


def _dense() -> list[float]:
    return [0.0] * BGE_M3_DENSE_DIM


def _span(**kw) -> SpanRecord:
    base = dict(span_id="c:0:h#0", parent_chunk_id="c:0:h", span_index=0, text="x",
                dense_vector=_dense(), sparse_vector={1: 0.5})
    base.update(kw)
    return SpanRecord(**base)


# --- SpanRecord CUAD extension (backward-compatible) ---

def test_span_record_defaults_are_backward_compatible():
    s = _span()  # no CUAD fields set -> ACORD leg unaffected
    assert s.contract_id == ""
    assert s.doc_start is None and s.doc_end is None and s.page is None and s.bbox is None


def test_span_record_with_offsets_and_bbox():
    s = _span(contract_id="C1", doc_start=100, doc_end=140, page=2, bbox=(1.0, 2.0, 3.0, 4.0))
    assert s.contract_id == "C1"
    assert (s.doc_start, s.doc_end) == (100, 140)
    assert s.page == 2 and s.bbox == (1.0, 2.0, 3.0, 4.0)


def test_span_record_rejects_reversed_offsets():
    with pytest.raises(ValidationError):
        _span(doc_start=140, doc_end=100)


def test_span_record_rejects_negative_start_and_zero_page():
    with pytest.raises(ValidationError):
        _span(doc_start=-1)
    with pytest.raises(ValidationError):
        _span(page=0)


# --- ContractRecord ---

def test_contract_record_minimal_and_full():
    assert ContractRecord(contract_id="C1").parties == []
    c = ContractRecord(contract_id="C1", name="Distributor Agreement", agreement_type="Distribution",
                       parties=["Acme", "Beta"], page_count=12)
    assert c.parties == ["Acme", "Beta"] and c.page_count == 12


# --- QueryIntent (NL->type) ---

def test_query_intent_canonicalizes_types_case_insensitively():
    qi = QueryIntent(clause_types=["governing law", "Parties"])
    assert qi.clause_types == ["Governing Law", "Parties"]  # canonicalized, order preserved


def test_query_intent_dedups_types():
    qi = QueryIntent(clause_types=["Governing Law", "governing law"])
    assert qi.clause_types == ["Governing Law"]


def test_query_intent_rejects_out_of_taxonomy_type():
    with pytest.raises(ValidationError):
        QueryIntent(clause_types=["Payment Schedule"])  # not a FUNCTION label


def test_query_intent_out_of_taxonomy_query_has_empty_types():
    qi = QueryIntent(clause_types=[], in_taxonomy=False, confidence=0.3)
    assert qi.clause_types == [] and qi.in_taxonomy is False and qi.intent == "highlight"


def test_query_intent_confidence_bounds():
    with pytest.raises(ValidationError):
        QueryIntent(confidence=1.5)


def test_query_intent_intents():
    assert QueryIntent(clause_types=["Cap On Liability"], intent="discriminate",
                       value_condition="seller-favorable").intent == "discriminate"


# --- HighlightSpan / HighlightResult ---

def test_highlight_span_offsets_and_value():
    hs = HighlightSpan(span_id="s", contract_id="C1", function="Governing Law", clause_ref="Section 20",
                       text="Governed by New York law", doc_start=10, doc_end=34, extracted_value="New York")
    assert hs.extracted_value == "New York"
    with pytest.raises(ValidationError):
        HighlightSpan(span_id="s", contract_id="C1", function="X", clause_ref="r", text="t",
                      doc_start=40, doc_end=10)


def test_highlight_result_present_must_match_spans():
    empty = HighlightResult(query="q", contract_id="C1", present=False)
    assert empty.present is False and empty.spans == []
    with pytest.raises(ValidationError):
        HighlightResult(query="q", contract_id="C1", present=True)  # present=True but no spans
