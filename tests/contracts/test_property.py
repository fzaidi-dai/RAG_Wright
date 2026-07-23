"""T57 (FR-C.6, ADR-0025): the clause PROPERTY schema contract.

Exercises the load-bearing invariants: closed-vocabulary validation with the AMBIGUOUS `other` escape,
open-valued dimensions, span-cited provenance (FR-S.4 / FR-Q.6), function-taxonomy membership, and the
clause-anchoring of assertions. Hermetic -- no store, no model.
"""

from __future__ import annotations

import pytest

from rag_wright.contracts.identifiers import ChunkId
from rag_wright.contracts.property import (
    CLOSED_VOCAB,
    FOLIO_CLAUSE_IRI,
    FOLIO_SUBJECT_IRI,
    ClausePropertyRecord,
    PropertyAssertion,
    PropertyDimension,
)
from rag_wright.contracts.provenance import ConfidenceTag, Provenance


def _prov(source: str = "doc1", index: int = 0, content: str = "clause body") -> Provenance:
    return Provenance.of(ChunkId.of(source, index, content))


def _assert(dimension: PropertyDimension, value: str, confidence: ConfidenceTag, prov: Provenance) -> PropertyAssertion:
    return PropertyAssertion(provenance=prov, confidence=confidence, dimension=dimension, value=value, span_id="")


def test_closed_vocab_value_extracted_ok():
    a = _assert(PropertyDimension.CARVE_OUT, "indemnification", ConfidenceTag.EXTRACTED, _prov())
    assert a.value == "indemnification" and a.dimension is PropertyDimension.CARVE_OUT


def test_out_of_vocab_value_rejected_unless_ambiguous():
    prov = _prov()
    with pytest.raises(ValueError, match="closed vocabulary"):
        _assert(PropertyDimension.CARVE_OUT, "act_of_god", ConfidenceTag.EXTRACTED, prov)
    # the 'other' escape: the same novel value IS admissible as AMBIGUOUS
    a = _assert(PropertyDimension.CARVE_OUT, "act_of_god", ConfidenceTag.AMBIGUOUS, prov)
    assert a.value == "act_of_god" and a.confidence is ConfidenceTag.AMBIGUOUS


def test_open_valued_dimension_accepts_any_nonempty_value():
    # jurisdiction is open-valued (not in CLOSED_VOCAB) -> any value with EXTRACTED is fine
    assert PropertyDimension.JURISDICTION not in CLOSED_VOCAB
    a = _assert(PropertyDimension.JURISDICTION, "england", ConfidenceTag.EXTRACTED, _prov())
    assert a.value == "england"


def test_empty_value_rejected():
    with pytest.raises(ValueError, match="non-empty"):
        _assert(PropertyDimension.MUTUALITY, "  ", ConfidenceTag.EXTRACTED, _prov())


def test_record_requires_function_in_taxonomy():
    prov = _prov()
    ok = ClausePropertyRecord(
        clause_id=str(prov.chunk_id), function="Cap On Liability",
        assertions=[_assert(PropertyDimension.MUTUALITY, "mutual", ConfidenceTag.EXTRACTED, prov)],
    )
    assert ok.function == "Cap On Liability" and len(ok.assertions) == 1
    # the 3 extensions are valid functions too
    assert ClausePropertyRecord(clause_id=str(prov.chunk_id), function="Indemnification").function == "Indemnification"
    with pytest.raises(ValueError, match="function taxonomy"):
        ClausePropertyRecord(clause_id=str(prov.chunk_id), function="Not A Clause Type")


def test_record_anchors_assertions_to_its_clause():
    prov_a = _prov("docA", 0, "clause A")
    prov_b = _prov("docB", 1, "clause B")
    # an assertion citing a different clause is rejected (no claim mis-cited, FR-Q.6)
    with pytest.raises(ValueError, match="anchored to the record's clause_id"):
        ClausePropertyRecord(
            clause_id=str(prov_a.chunk_id), function="Cap On Liability",
            assertions=[_assert(PropertyDimension.MUTUALITY, "mutual", ConfidenceTag.EXTRACTED, prov_b)],
        )


def test_folio_maps_cover_extensions_and_align_only_where_a_home_exists():
    base = "https://folio.openlegalstandard.org/"
    # the 3 extensions each got a verified FOLIO clause IRI
    for f in ("Indemnification", "Indirect/Consequential Damages Waiver", "Warranty Disclaimer"):
        assert FOLIO_CLAUSE_IRI[f].startswith(base)
    assert FOLIO_CLAUSE_IRI["Cap On Liability"].startswith(base)
    # the two types with no clean FOLIO home are deliberately absent (native, no IRI)
    assert "Joint IP Ownership" not in FOLIO_CLAUSE_IRI
    assert "Revenue/Profit Sharing" not in FOLIO_CLAUSE_IRI
    # subject IRIs are all carve-out vocabulary members
    assert set(FOLIO_SUBJECT_IRI) <= CLOSED_VOCAB[PropertyDimension.CARVE_OUT]
